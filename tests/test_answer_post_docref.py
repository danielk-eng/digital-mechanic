"""Expanded answer said «код F08 по 236-3902150-Б РЭ, стр. PDF 52 относится к…» (Vercel 27.11 01:20, 1200 px): internal
document code and «стр. PDF» are index jargon, the page chip shows the page. clean() drops them, the sentence stays."""
from digital_mechanic.agent.answer_post import clean, strip_doc_refs


def test_doc_code_and_pdf_page_removed_sentence_kept():
    t, _ = clean("**Вероятная причина** — код F08 по 236-3902150-Б РЭ, стр. PDF 52 относится к топливной системе [S1].")
    assert t == "**Вероятная причина** — код F08 относится к топливной системе [S1]."
    assert strip_doc_refs("Момент 118–147 Н·м (стр. PDF 53) [S2].") == "Момент 118–147 Н·м (стр. 53) [S2]."
    assert strip_doc_refs("Давление 400-700 кПа [S1].") == "Давление 400-700 кПа [S1]."  # ranges are not codes
    assert strip_doc_refs("См. 236-1000001-Б РЭ.") == "См. 236-1000001-Б РЭ."  # a bare reference is left alone
