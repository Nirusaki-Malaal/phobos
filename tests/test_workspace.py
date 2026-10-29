import pytest

from phobos.workspace import atomic_text, confined, project_path


def test_traversal_and_project_aliases(tmp_path):
    root = tmp_path / "workspace"
    project = project_path(root, "personal")
    project.mkdir(parents=True)
    with pytest.raises(ValueError):
        project_path(root, "../outside")
    (root / "alias").symlink_to(project, target_is_directory=True)
    with pytest.raises(ValueError):
        project_path(root, "alias")
    with pytest.raises(ValueError):
        confined(project, "../another-project/file")
    (project / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        confined(project, "escape/file")


def test_hidden_secret_alias_and_atomic_write(tmp_path):
    atomic_text(tmp_path / ".env", "private")
    (tmp_path / "innocent.txt").symlink_to(tmp_path / ".env")
    for name in [".env", "innocent.txt", "id.key", "nested/.git/config"]:
        with pytest.raises(ValueError):
            confined(tmp_path, name)
    output = confined(tmp_path, "notes/hello.md")
    atomic_text(output, "Version one")
    atomic_text(output, "Version two")
    assert output.read_text() == "Version two"
