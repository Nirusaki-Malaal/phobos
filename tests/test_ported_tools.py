from phobos import bridge


def test_all_skills_are_discoverable_and_supporting_files_readable(settings):
    available = bridge.skills(settings.extension_root)
    assert len(available) == 70
    assert {"name": "research"} in available
    assert "Research" in bridge.skill_view(settings.extension_root, "research")["content"]


def test_native_notebook_and_adr_project_isolation(settings):
    created = bridge.invoke(
        settings,
        "alpha",
        "pai_notebook_ops",
        {
            "action": "create_notebook",
            "notebook_name": "Research",
        },
    )
    assert "error" not in created, created
    listing = bridge.invoke(settings, "alpha", "pai_notebook_ops", {"action": "list_notebooks"})
    assert listing
    assert bridge.invoke(settings, "beta", "pai_notebook_ops", {"action": "list_notebooks"}) == []
    adr = bridge.invoke(
        settings,
        "alpha",
        "pai_adr_ops",
        {
            "action": "create_adr",
            "adr_number": "001",
            "title": "Use local storage",
            "context": "Private data",
            "decision": "SQLite and local files",
            "symbols": [],
        },
    )
    assert "error" not in adr, adr
    assert list((settings.workspace_dir / "alpha/.project/planning").rglob("ADR-*.md"))
    assert not (settings.workspace_dir / "beta/.project/planning").exists()


def test_native_tool_cannot_override_project_or_read_other_files(settings):
    import pytest

    with pytest.raises(ValueError):
        bridge.invoke(
            settings,
            "alpha",
            "pai_notebook_ops",
            {
                "action": "add_source_file",
                "file_path": "../beta/secrets.txt",
            },
        )
    with pytest.raises(ValueError):
        bridge.invoke(
            settings,
            "alpha",
            "pai_notebook_ops",
            {
                "action": "get_notebook",
                "notebook_id": "../../beta",
            },
        )
