"""Offline PDF corpus evaluation. No network, AI keys or extracted text in report.

Usage: python tools/evaluate_pdfs.py /path/to/pdfs --output report.json
Optional: --expected expected.json (a SHA256 -> reference count mapping).
Reports parsing availability; exact-count checks require independent expected counts.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extractor import (ExtractionError, extract_bibliography_text, extract_references,
                       numbered_bibliography_entries, split_bibliography_batches)


def evaluate(paths, expected):
    rows, seen = [], set()
    for path in sorted(paths):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        row = {'file': path.name, 'sha256': digest}
        try:
            refs = extract_references(data)
            candidate = extract_bibliography_text(data)
            labels = numbered_bibliography_entries(candidate)
            row.update(status='parsed', references=len(refs),
                       numbered_source_entries=len(labels),
                       source_number_gaps=sorted(set(range(1, max(labels, default=0) + 1)) - labels.keys()),
                       ai_batches=len(split_bibliography_batches(candidate)),
                       entries_without_parsed_title=sum(not r.title for r in refs))
            if digest in expected:
                row.update(expected_references=expected[digest], count_matches=len(refs) == expected[digest])
        except ExtractionError as exc:
            row.update(status='unsupported_or_no_bibliography', reason=str(exc))
            if digest in expected:
                row.update(expected_references=expected[digest], count_matches=False)
        rows.append(row)
        print(f"{path.name}: {row['status']} ({row.get('references', '-')})", flush=True)
    return {'mode': 'offline; no live AI or academic API calls',
            'note': 'Parsed does not prove completeness or title accuracy. count_matches requires independent expected counts.',
            'unique_pdfs': len(rows), 'parsed': sum(r['status'] == 'parsed' for r in rows),
            'exact_count_checks': sum('count_matches' in r for r in rows), 'files': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected', type=Path)
    args = parser.parse_args()
    files = []
    for path in args.paths:
        files.extend(path.rglob('*.pdf') if path.is_dir() else [path])
    expected = json.loads(args.expected.read_text()) if args.expected else {}
    if not isinstance(expected, dict) or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in expected.values()):
        parser.error('expected must map SHA256 values to nonnegative integer counts')
    result = evaluate(files, expected)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if any(r.get('count_matches') is False for r in result['files']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
