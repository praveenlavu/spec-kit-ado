"""Raw `System.Description` HTML read back from the smoke org with `ado get` (V3 piece 3, P3-1), used by the rules parser tests.

PROVENANCE, stated plainly: the owner chose not to type or paste these in the ADO web editor, so the three Descriptions were SET
THROUGH THE REST API (`ado update --field System.Description=...`) with HTML shaped like what ADO's editor and a Word paste produce, and
then read back. ADO's server-side sanitiser really ran over them (it dropped the conditional comments, the `<xml>` block and every
`<o:p>`), but the markup is synthetic: it is NOT real editor output and NOT a real Word clipboard. A real Word paste would also carry
`mso-*` style attributes, `<span lang=...>` wrappers and `file:///` image references. Review Focus 1 (a PO's pasted rich text) is therefore
only partly covered until someone repeats the capture with a real paste. Ids, the organisation and names are fictional: the work items
behind the files were an Epic, two Features and two Stories in a test project."""
import pathlib

import pytest

FIX = pathlib.Path(__file__).parent / "fixtures" / "rules"
NAMES = ("epic_po_typed", "feature_po_typed", "feature_word_paste")


def read(name):
    return (FIX / (name + ".html")).read_bytes().decode("utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_fixtures_are_raw_ado_html(name):
    raw = (FIX / (name + ".html")).read_bytes()
    assert len(raw) < 256 * 1024
    text = raw.decode("utf-8")
    assert "Business rules" in text and "Rule 1:" in text
    for leak in ("praveenlavu", "dev.azure.com", "/Users/", "C:\\Users", "@", "file:///"):
        assert leak not in text, leak


def test_typed_fixtures_have_the_editor_shapes():
    epic, feat = read("epic_po_typed"), read("feature_po_typed")
    assert "<div><b>Business rules</b> </div>" in epic and "Rule 1: Members must be 18 or older." in epic
    assert "<li><div><b>Rule 1:</b>" in feat  # a bullet whose text sits in a div
    assert "<b>Status </b>: Approved" in feat and "Points can't pay for delivery." in feat  # `status : Approved`; ADO unescaped the apostrophe
    assert "<b>Rule:</b> Points earned in a returned order are removed." in feat  # a rule with no number
    assert feat.count("<b>Rule ") == 3 and "Rule 2:" in feat and "draft" in feat


def test_word_fixture_asserts_exactly_the_artefacts_that_survived_ado():
    word = read("feature_word_paste")
    for kept in ("<style", "class=MsoNormal", "class=MsoListParagraph", "&nbsp;", "\u200b", "font-family:Symbol", "\u00b7", "Rule 3 -"):
        assert kept in word, kept  # note `class=MsoNormal` is unquoted and `&middot;` came back as a literal middle dot: ADO re-serialised the HTML
    for stripped in ("<!--", "<xml", "<o:p>", "mso-", "file:///", "&middot;"):  # removed by ADO's sanitiser, or never present in this synthetic text
        assert stripped not in word, stripped
