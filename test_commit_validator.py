import unittest
from commit_validator import validate_commit_message


class TestCommitValidator(unittest.TestCase):
    def test_valid_commit(self):
        errors = validate_commit_message("feat(scope): añadir funcionalidad útil")
        self.assertEqual(errors, [])

    def test_invalid_type(self):
        errors = validate_commit_message("invalid(scope): cambiar algo")
        self.assertTrue(any("Conventional Commits" in e for e in errors))

    def test_header_too_long(self):
        long_desc = "a" * 70
        header = f"feat: {long_desc}"
        errors = validate_commit_message(header)
        self.assertTrue(any("máximo 72" in e for e in errors))

    def test_uppercase_description(self):
        errors = validate_commit_message("fix: Cambiar mayúscula")
        self.assertTrue(any("minúscula" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
