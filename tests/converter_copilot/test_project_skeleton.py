def test_converter_copilot_package_is_importable():
    import converter_copilot

    assert converter_copilot.__name__ == "converter_copilot"


def test_upstream_graph_remains_importable():
    from open_deep_research.deep_researcher import deep_researcher

    assert deep_researcher is not None
