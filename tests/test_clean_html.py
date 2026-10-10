import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from vulcan_messages import clean_html

class TestCleanHtml(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(clean_html(""), "")
        self.assertEqual(clean_html(None), "")

    def test_strip_basic_tags(self):
        html_input = "<p>Szanowni Państwo,</p><p>Przekazuję informację.</p>"
        expected = "Szanowni Państwo,\nPrzekazuję informację."
        self.assertEqual(clean_html(html_input), expected)

    def test_br_tags(self):
        html_input = "Linia 1<br>Linia 2<br/>Linia 3<BR />Linia 4"
        expected = "Linia 1\nLinia 2\nLinia 3\nLinia 4"
        self.assertEqual(clean_html(html_input), expected)

    def test_strip_nested_and_links(self):
        html_input = '<div>Więcej informacji <a href="https://example.com">tutaj</a>.</div>'
        expected = "Więcej informacji tutaj."
        self.assertEqual(clean_html(html_input), expected)

    def test_whitespace_and_excessive_newlines(self):
        html_input = "   <p>Tekst 1</p>\n\n\n\n\n<p>Tekst 2</p>   "
        expected = "Tekst 1\n\nTekst 2"
        self.assertEqual(clean_html(html_input), expected)

if __name__ == "__main__":
    unittest.main()
