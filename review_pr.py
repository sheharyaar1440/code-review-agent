import ollama
import git
import os
import sys
import re
import json


def get_pr_diff(repo_path='.'):
    try:
        repo = git.Repo(repo_path)
        current_branch = repo.active_branch.name
        diff = repo.git.diff('main', current_branch)
        return diff
    except Exception as e:
        return f"Error getting diff: {str(e)}"


def is_reviewable_file(path: str) -> bool:
    """Return True if the path should be reviewed (project source files only).

    Framework-agnostic: supports many languages and does not require a specific directory,
    but excludes well-known infra/config paths.
    """
    if not isinstance(path, str):
        return False
    # Exclude config, scripts, and non-project areas
    excluded_prefixes = (
        '.github/',
        '.husky/',
        'scripts/',
        'config/',
        'node_modules/',
        'public/',
        '__tests__/',
        '.vscode/',
        '.idea/',
        '.git/',
    )
    if path.startswith(excluded_prefixes):
        return False
    excluded_files = (
        'review_pr.py',
        'review.py',
    )
    if any(path.endswith(name) for name in excluded_files):
        return False
    # Allowed language extensions (broad set)
    allowed_exts = (
        '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte',
        '.py', '.rb', '.php', '.go', '.rs', '.java', '.kt', '.kts', '.scala',
        '.cs', '.cshtml', '.vb', '.fs', '.swift',
        '.c', '.cc', '.cxx', '.cpp', '.h', '.hpp', '.hh', '.m', '.mm',
        '.sql', '.ps1', '.sh'
    )
    return path.endswith(allowed_exts)


def parse_unified_diff(diff_text: str):
    """Parse a unified diff and return a mapping of file -> set of new line numbers that were added.

    Only tracks added lines ('+' lines) and their new-file line numbers from each hunk.
    """
    file_path = None
    added_lines_by_file = {}
    new_line_number = None

    # Patterns
    diff_file_header = re.compile(r"^\+\+\+ b\/(.+)$")
    hunk_header = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@")

    for raw_line in diff_text.splitlines():
        line = raw_line.rstrip("\n")

        file_match = diff_file_header.match(line)
        if file_match:
            candidate_path = file_match.group(1)
            # Only review eligible project files
            if is_reviewable_file(candidate_path):
                file_path = candidate_path
                if file_path not in added_lines_by_file:
                    added_lines_by_file[file_path] = set()
            else:
                file_path = None
            new_line_number = None
            continue

        hunk_match = hunk_header.match(line)
        if hunk_match:
            # Start of a new hunk. Initialize new_line_number to the starting line in the new file
            new_line_number = int(hunk_match.group(1))
            continue

        if file_path is None or new_line_number is None:
            # We are not inside a file hunk
            continue

        if line.startswith('+++') or line.startswith('---') or line.startswith('diff --git'):
            # Headers already handled; skip
            continue

        if line.startswith('+') and not line.startswith('+++'):
            # Added line in the new file
            added_lines_by_file[file_path].add(new_line_number)
            new_line_number += 1
        elif line.startswith('-') and not line.startswith('---'):
            # Removed line; does not advance new file line number
            continue
        else:
            # Context line
            new_line_number += 1

    return added_lines_by_file


def build_review_prompt(added_lines_by_file: dict) -> str:
    """Construct a precise, framework-agnostic prompt instructing the model to comment only on added lines with exact file and line numbers."""
    parts = []
    parts.append(
        "You are an expert software code reviewer across languages and frameworks. Review ONLY the added lines below."
    )
    parts.append("For each issue, return a JSON array of objects of the form:")
    parts.append(
        '{"file": "relative/path.js", "line": <added_line_number>, "comment": "specific, actionable suggestion"}')
    parts.append("Rules:")
    parts.append(
        "- Only use file paths and line numbers exactly as provided below.")
    parts.append(
        "- Keep comments concise and actionable; avoid restating the code.")
    parts.append(
        "- Focus on correctness, performance, security, maintainability, and style.")
    parts.append("")
    parts.append("Changed files and added lines:")

    for file_path, line_numbers in added_lines_by_file.items():
        if not line_numbers:
            continue
        sorted_lines = sorted(line_numbers)
        parts.append(f"- {file_path}: added lines {sorted_lines}")

    parts.append("")
    parts.append(
        "Return ONLY valid JSON array with the specified fields. No explanations.")
    return "\n".join(parts)


def extract_json_array(text: str):
    """Best-effort extraction of the first JSON array from a text block."""
    try:
        # Fast path: already pure JSON array
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        pass

    # Try to find the first [...] block
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        candidate = text[start: end + 1]
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def filter_items_to_changed_lines(items, added_lines_by_file):
    """Keep only items that target a changed file and an added line number."""
    filtered = []
    for item in items or []:
        try:
            file_path = item.get('file')
            line_number = int(item.get('line'))
            comment = str(item.get('comment', '')).strip()
        except Exception:
            continue

        if not file_path or not isinstance(file_path, str):
            continue
        if not comment:
            continue
        if not is_reviewable_file(file_path):
            continue
        if file_path not in added_lines_by_file:
            continue
        if line_number not in added_lines_by_file[file_path]:
            continue
        filtered.append({
            'file': file_path,
            'line': line_number,
            'comment': comment
        })
    return filtered


def rule_based_review(file_path: str, added_lines: set[int]):
    """Static checks on added lines for common React/JS issues.

    Reads file content from disk and creates comments only for added lines.
    """
    results = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            src = f.read()
    except Exception:
        return results

    lines = src.splitlines()

    def add(line_no: int, msg: str):
        if line_no in added_lines:
            results.append(
                {'file': file_path, 'line': line_no, 'comment': msg})

    # Generic heuristics
    for idx, text in enumerate(lines, start=1):
        stripped = text.strip()

        # TODO/FIXME
        if re.search(r"\b(TODO|FIXME)\b", stripped, re.IGNORECASE):
            add(idx, "Found TODO/FIXME. Resolve or track before merging.")

        # Debug logging
        if re.search(r"console\.(log|debug|trace)\(|System\.out\.println\(|Debug\.Write(Line)?\(|print\(", stripped):
            add(idx, "Remove debug logging before commit or guard behind env flag.")

        # Potential secret
        if re.search(r"(API|SECRET|TOKEN|KEY)\s*[:=]", stripped, re.IGNORECASE):
            add(idx, "Potential secret in code. Use env vars or secret manager.")

        # JS/TS strict equality
        if file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".vue")) and re.search(r"[^=!]==[^=]", stripped):
            add(idx, "Use strict equality (===) in JS/TS to avoid coercion.")

        # Async call without await/then (heuristic)
        if re.search(r"\b(fetch|axios\.(get|post|put|delete))\(", stripped) and not re.search(r"await\s+|\.then\(", stripped):
            add(idx, "Async call without await/then. Ensure promise is handled.")

        # C# public fields (heuristic)
        if file_path.endswith('.cs') and re.search(r"public\s+\w+\s+\w+\s*;", stripped):
            add(idx, "Prefer properties (get/set) instead of public fields in C#.")

        # C# string concatenation in loops (heuristic)
        if file_path.endswith('.cs') and re.search(r"\+\=\s*\w+\s*\+", stripped):
            add(idx, "Avoid string concatenation in loops; use StringBuilder in C#.")

        # Vue inline handlers
        if file_path.endswith('.vue') and re.search(r"@click=\"\w+\(\)\"", stripped):
            add(idx, "Prefer extracting handlers and using modifiers in Vue for clarity.")

        # TS any type
        if file_path.endswith('.ts') and re.search(r":\s*any\b", stripped):
            add(idx, "Avoid 'any' in TypeScript; use specific types or unknown.")

    # Framework-specific checks removed to keep it generic

    return results


def review_code(diff):
    if not diff or diff.startswith("Error getting diff:"):
        return []

    added_lines_by_file = parse_unified_diff(diff)
    # First, run rule-based checks for determinism
    rule_based_items = []
    for file_path, added_lines in added_lines_by_file.items():
        if not added_lines:
            continue
        try:
            file_items = rule_based_review(file_path, added_lines)
            rule_based_items.extend(file_items)
        except Exception:
            # Ignore rule engine failures per file
            continue

    if rule_based_items:
        return rule_based_items

    # Fallback to LLM if rules found nothing
    prompt = build_review_prompt(added_lines_by_file)
    try:
        client = ollama.Client(host='http://127.0.0.1:11434')
        response = client.generate(
            model='codellama:7b-instruct', prompt=prompt)
        raw_text = response.get('response', '')
        items = extract_json_array(raw_text)
        return filter_items_to_changed_lines(items, added_lines_by_file)
    except Exception:
        return []


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--github':
        diff = os.environ.get('PR_DIFF', get_pr_diff())
    else:
        diff = get_pr_diff()

    review = review_code(diff)
    print(json.dumps(review, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps(
            [{"file": "UNKNOWN", "line": 1, "comment": f"Fatal error: {str(e)}"}]))
        sys.exit(1)
