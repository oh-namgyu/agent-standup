"""Label shortening and redaction-fallback tests."""

import unittest
from pathlib import Path

from agent_standup.labels import HOME_LABEL, NO_PATH_LABEL, pretty, pretty_path


class PrettyTestCase(unittest.TestCase):
    def test_empty_path_is_the_no_cwd_label(self):
        self.assertEqual(pretty(""), NO_PATH_LABEL)
        self.assertEqual(pretty(None), NO_PATH_LABEL)

    def test_home_prefix_is_stripped_before_shortening(self):
        self.assertEqual(pretty("/Users/alice/code/app", redact=False), "code-app")

    def test_windows_style_separators_are_normalized(self):
        self.assertEqual(pretty("C:\\Users\\alice\\code\\app", redact=False), "code-app")

    def test_only_last_two_segments_are_kept(self):
        self.assertEqual(pretty("/Users/alice/a/b/c/d", redact=False), "c-d")

    def test_root_after_home_strip_falls_back_to_home_label(self):
        self.assertEqual(pretty("/Users/alice", redact=False), HOME_LABEL)

    def test_account_name_elsewhere_in_the_path_is_still_masked(self):
        # The redactor's account-name term is *this* machine's home directory
        # name, so use the real one instead of a synthetic "alice" fixture.
        account = Path.home().name
        if len(account) < 3:
            self.skipTest("home account name too short for the redactor to target")
        label = pretty("/srv/%s/project" % account, redact=True)
        self.assertNotIn(account, label)

    def test_redaction_can_be_disabled(self):
        # Home-prefix stripping always happens; --no-redact only skips the
        # secret-pattern and account-name pass that runs after it.
        self.assertEqual(pretty("/srv/alice/code", redact=False), "alice-code")

    def test_non_home_path_keeps_its_own_last_segments(self):
        self.assertEqual(pretty("/srv/repos/agent-standup", redact=False), "repos-agent-standup")


class PrettyPathTestCase(unittest.TestCase):
    def test_masks_the_full_path_when_redaction_is_on(self):
        self.assertNotIn("/Users/", pretty_path("/Users/alice/code", redact=True))

    def test_leaves_the_path_untouched_when_redaction_is_off(self):
        self.assertEqual(pretty_path("/Users/alice/code", redact=False), "/Users/alice/code")


if __name__ == "__main__":
    unittest.main()
