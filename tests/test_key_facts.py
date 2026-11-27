

def test_column_by_column_table_cells_are_separate_statements():
    # ЯМЗ-236 РЭ p. 51 as pdftotext gives it: remedies one per cell, no punctuation between them
    page = ("е) при невозможности устранения\nдефекта заменить стартер\nПромыть заборник, промыть и\nпродуть топливопроводы\n"
            "Осторожно прогреть топливные\nтрубки, фильтры и бак\nЗаменить топливо другим, соответствующим сезону, и прокачать\nсистему")
    from digital_mechanic.agent.facts import sentences
    ss = sentences(page)
    assert "Осторожно прогреть топливные трубки, фильтры и бак" in ss and "Промыть заборник, промыть и продуть топливопроводы" in ss
    assert not any("стартер Промыть" in s for s in ss)
