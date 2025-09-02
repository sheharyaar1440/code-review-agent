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
            file_path = file_match.group(1)
            if file_path not in added_lines_by_file:
                added_lines_by_file[file_path] = set()
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
    """Construct a precise prompt instructing the model to comment only on added lines with exact file and line numbers."""
    parts = []
    parts.append(
        "You are an expert React and JavaScript code reviewer. Review ONLY the added lines below."
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
        "- Focus on correctness, React best practices, performance, security, and style.")
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


def review_code(diff):
    if not diff or diff.startswith("Error getting diff:"):
        return []

    added_lines_by_file = parse_unified_diff(diff)
    prompt = build_review_prompt(added_lines_by_file)

    try:
        client = ollama.Client(host='http://127.0.0.1:11434')
        response = client.generate(
            model='codellama:7b-instruct', prompt=prompt)
        raw_text = response.get('response', '')
        items = extract_json_array(raw_text)
        return filter_items_to_changed_lines(items, added_lines_by_file)
    except Exception:
        # If the model call fails, return empty so the workflow posts a summary only
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
