import ollama
import git
import os
import sys
import time


def get_pr_diff(repo_path='.'):
    try:
        repo = git.Repo(repo_path)
        current_branch = repo.active_branch.name
        # Adjust 'main' if your base branch is different
        diff = repo.git.diff('main', current_branch)
        return diff
    except Exception as e:
        return f"Error getting diff: {str(e)}"


def review_code(diff):
    if not diff:
        return "No changes to review."
    prompt = f"""
    You are an expert code reviewer. Review the following code changes (diff) for:
    - Bugs or errors
    - Code style and best practices
    - Performance improvements
    - Security issues
    - Suggestions for better code

    Provide comments in a numbered list, with line references if possible. Be constructive and specific.

    Diff:
    {diff}
    """
    try:
        # Matches your Ollama host
        client = ollama.Client(host='http://127.0.0.1:11434')
        response = client.generate(
            model='codellama:7b-instruct', prompt=prompt)
        return response['response']
    except Exception as e:
        return f"Error connecting to Ollama: {str(e)}"


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--github':
        diff = os.environ.get('PR_DIFF', get_pr_diff())
    else:
        diff = get_pr_diff()

    if not diff:
        print("No changes detected.")
        return

    review = review_code(diff)
    print("### AI Code Review\n" + review)


if __name__ == '__main__':
    main()
