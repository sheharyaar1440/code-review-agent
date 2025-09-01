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


def review_code(diff):
    if not diff:
        return []

    prompt = f"""
    You are an expert React and JavaScript code reviewer. Review the following code changes (diff) for:
    - Bugs
    - React best practices
    - Code style
    - Performance
    - Security
    Return output as JSON array of objects with:
    - file: file path (guess from diff header if possible, otherwise 'UNKNOWN')
    - line: line number in new code
    - comment: the review comment
    Diff:
    {diff}
    """

    try:
        client = ollama.Client(host='http://127.0.0.1:11434')
        response = client.generate(
            model='codellama:7b-instruct', prompt=prompt)
        review = response['response']

        # Assume model returns JSON or text we can eval/parse
        try:
            parsed = json.loads(review)
            return parsed
        except Exception:
            # fallback: wrap in one object
            return [{"file": "UNKNOWN", "line": 1, "comment": review}]
    except Exception as e:
        return [{"file": "UNKNOWN", "line": 1, "comment": f"Error: {str(e)}"}]


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--github':
        diff = os.environ.get('PR_DIFF', get_pr_diff())
    else:
        diff = get_pr_diff()

    review = review_code(diff)
    print(json.dumps(review, indent=2))


if __name__ == '__main__':
    main()
