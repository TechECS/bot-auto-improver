import unittest
from git_context import calculate_file_priority, rank_files_by_activity


class TestGitContext(unittest.TestCase):
    def test_calculate_file_priority_readme(self):
        score = calculate_file_priority("README.md")
        self.assertGreaterEqual(score, 10.0)

    def test_calculate_file_priority_code_with_churn(self):
        git_scores = {"src/app.py": 1.0, "src/old.py": 0.0}
        score_active = calculate_file_priority("src/app.py", git_scores)
        score_inactive = calculate_file_priority("src/old.py", git_scores)
        self.assertGreater(score_active, score_inactive)

    def test_rank_files_by_activity(self):
        files = ["docs/info.txt", "src/main.py", "README.md"]
        git_scores = {"src/main.py": 0.9}
        ranked = rank_files_by_activity(files, git_scores=git_scores)
        self.assertEqual(ranked[0], "README.md")
        self.assertEqual(ranked[1], "src/main.py")
        self.assertEqual(ranked[2], "docs/info.txt")


if __name__ == "__main__":
    unittest.main()
