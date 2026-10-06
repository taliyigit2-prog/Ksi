import runpy
import unittest
from pathlib import Path


ArticleText = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/snapshot_model_terms.py"))["ArticleText"]


class ModelTermsSnapshotTests(unittest.TestCase):
    def test_snapshot_has_only_article_text_and_survives_void_tags(self):
        parser = ArticleText()
        parser.feed('<header>outside</header><div class="devsite-article-body"><p>First<br/>Second<img src="example"/></p><script>active code</script><p>Third &amp; Fourth</p></div><footer>outside</footer>')
        result = "".join(parser.parts)
        for word in ("First", "Second", "Third & Fourth"):
            self.assertIn(word, result)
        for word in ("outside", "active code"):
            self.assertNotIn(word, result)
