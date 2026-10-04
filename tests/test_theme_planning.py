"""Theme planning accepts explicit equivalent names without guessing memberships."""
import json
import os
from pathlib import Path
import unittest

from fastapi import HTTPException

from ai_cluster_batches import cluster_in_batches, parse_themes
from extractor import extract_references


NAMES = ["Battery degradation", "Pulse-response diagnostics", "Battery recycling"]
THEMES = [{"id": index, "name": name} for index, name in enumerate(NAMES, 1)]


class ThemePlanningFormatTests(unittest.TestCase):
    def test_one_to_eight_actual_themes_are_preserved(self):
        for count in range(1, 9):
            names = [f"Research theme {index}" for index in range(count)]
            with self.subTest(count=count):
                result = parse_themes({"themes": names})
                self.assertEqual([row["name"] for row in result], names)
                self.assertEqual([row["id"] for row in result], list(range(1, count + 1)))
        for rows in ([], [f"Theme {index}" for index in range(9)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                parse_themes({"themes": rows})

    def test_explicit_name_lists_and_aliases(self):
        self.assertEqual(parse_themes(NAMES), THEMES)
        for root in ("themes", "topics", "clusters"):
            for alias in ("name", "theme", "topic", "label", "title", "theme_name"):
                with self.subTest(root=root, alias=alias):
                    result = parse_themes({root: [{alias: name} for name in NAMES]})
                    self.assertEqual(result, THEMES)

    def test_agreeing_aliases_are_safe_but_conflicting_names_are_rejected(self):
        rows = [{"name": name, "label": f" {name} "} for name in NAMES]
        self.assertEqual(parse_themes({"themes": rows}), THEMES)
        rows[0]["label"] = "A different theme"
        with self.assertRaises(ValueError):
            parse_themes({"themes": rows})

    def test_explicit_theme_mapping_has_canonical_ids(self):
        value = {"themes": {f"T{index}": name for index, name in enumerate(NAMES, 1)}}
        self.assertEqual(parse_themes(value), THEMES)

    def test_positive_plan_ids_are_preserved(self):
        rows = [{"id": identifier, "name": name} for identifier, name in zip((7, "12", 24), NAMES)]
        self.assertEqual(parse_themes({"themes": rows}), [
            {"id": identifier, "name": name} for identifier, name in zip((7, 12, 24), NAMES)
        ])

    def test_zero_based_or_text_ids_reindex_the_whole_plan(self):
        for identifiers in ((0, 1, 2), ("T1", "T2", "T3"), (9, "T2", 4)):
            with self.subTest(identifiers=identifiers):
                value = {"themes": [{"id": identifier, "name": name}
                                    for identifier, name in zip(identifiers, NAMES)]}
                self.assertEqual(parse_themes(value), THEMES)

    def test_duplicate_names_and_explicit_ids_are_rejected(self):
        values = [
            {"themes": [NAMES[0], f" {NAMES[0].upper()} ", NAMES[2]]},
            {"themes": [{"id": 1, "name": NAMES[0]}, {"id": 1, "name": NAMES[1]}]},
            {"themes": [{"id": "T1", "name": NAMES[0]}, {"id": "T1", "name": NAMES[1]}]},
            {"themes": [{"id": 0, "name": NAMES[0]}, {"id": 0, "name": NAMES[1]}]},
            {"themes": [{"id": True, "name": NAMES[0]}, {"id": 2, "name": NAMES[1]}]},
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_themes(value)

    def test_json_fences_and_prefixed_json_are_read_without_a_second_request(self):
        data = json.dumps({"themes": NAMES})
        for text in (data, f"```json\n{data}\n```", f"Here are the themes:\n{data}"):
            with self.subTest(text=text):
                self.assertEqual(parse_themes(text), THEMES)

    def test_clean_numbered_and_bullet_theme_lists(self):
        for prefix in ("-", "*", "•"):
            text = "\n".join(f"{prefix} {name}" for name in NAMES)
            with self.subTest(prefix=prefix):
                self.assertEqual(parse_themes(text), THEMES)
        for punctuation in (".", ")"):
            text = "\n".join(f"{index}{punctuation} {name}" for index, name in enumerate(NAMES, 1))
            with self.subTest(punctuation=punctuation):
                self.assertEqual(parse_themes(text), THEMES)

    def test_complete_reasoning_blocks_are_excluded_before_plain_lists(self):
        final_list = "- Methods\n- Recycling"
        expected = [{"id": 1, "name": "Methods"}, {"id": 2, "name": "Recycling"}]
        for tag in ("think", "analysis"):
            with self.subTest(tag=tag):
                self.assertEqual(parse_themes(f"<{tag}>{{example}}</{tag}>\n{final_list}"), expected)
                with self.assertRaises(ValueError):
                    parse_themes(f"<{tag}>{{example}}\n{final_list}")

    def test_prose_truncated_or_ambiguous_payloads_do_not_become_theme_names(self):
        values = [
            "This research covers battery degradation and recycling. More evidence is needed.",
            "I cannot determine the themes from these titles.",
            '{"themes":[{"name":"Battery degradation"},',
            '{"themes":["Battery degradation","Battery recycling"]',
            json.dumps({"themes": NAMES}) + "\n{}",
            {"themes": NAMES, "topics": ["Different theme"]},
            {"themes": [{"name": ""}]},
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_themes(value)


class ThemePlanningRequestTests(unittest.IsolatedAsyncioTestCase):
    async def run_mocked_catalogue(self, papers):
        calls = []

        async def generate(*args, **kwargs):
            calls.append((args, kwargs))
            prompt = args[3]
            if len(calls) == 1:
                self.assertIs(kwargs.get("response_parser"), parse_themes)
                self.assertTrue(kwargs["structured"])
                # The original response is not JSON, but names are explicit.
                return kwargs["response_parser"]("\n".join(f"- {name}" for name in NAMES))
            batch = json.loads(prompt.split("\nPapers:\n", 1)[1])
            self.assertLessEqual(len(batch), 12)
            return {"assignments": [{"id": row["id"], "theme_id": 1} for row in batch]}

        result = await cluster_in_batches(papers, "maxplus", "test", "fake-key", None, generate)
        expected_ids = {paper["id"] for paper in papers}
        actual_ids = [identifier for group in result["clusters"] for identifier in group["ids"]]
        self.assertEqual(set(actual_ids), expected_ids)
        self.assertEqual(len(actual_ids), len(expected_ids))
        self.assertEqual(result["unassigned_ids"], [])
        self.assertTrue(result["clustering_complete"])
        self.assertEqual(len(calls), result["clustering_batches"] + 1)
        self.assertEqual(result["clustering_requests"], len(calls))
        return result

    async def test_large_catalogue_uses_raw_plan_parser_and_no_paid_retry(self):
        papers = [{"id": index * 3, "title": f"Battery research {index}"}
                  for index in range(1, 53)]
        result = await self.run_mocked_catalogue(papers)
        self.assertEqual(result["clustering_batches"], 5)

    async def test_unreadable_plan_stops_after_one_request(self):
        calls = 0

        async def generate(*args, **kwargs):
            nonlocal calls
            calls += 1
            return kwargs["response_parser"]("Cannot determine themes from this response.")

        papers = [{"id": index, "title": f"Battery research {index}"} for index in range(1, 53)]
        with self.assertRaises(HTTPException) as caught:
            await cluster_in_batches(papers, "maxplus", "test", "fake-key", None, generate)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("แผนธีม", caught.exception.detail)
        self.assertEqual(calls, 1)

    @unittest.skipUnless(os.environ.get("PAPERREF_CLUSTER_PDF"), "User PDF fixture is opt-in")
    async def test_user_52_reference_pdf_planning_and_assignments(self):
        fixture = Path(os.environ["PAPERREF_CLUSTER_PDF"])
        references = extract_references(fixture.read_bytes())
        self.assertEqual(len(references), 52)
        papers = [{"id": reference.number or index,
                   "title": reference.title or reference.original_text[:200]}
                  for index, reference in enumerate(references, 1)]
        result = await self.run_mocked_catalogue(papers)
        self.assertEqual(result["clustering_batches"], 5)


if __name__ == "__main__":
    unittest.main()
