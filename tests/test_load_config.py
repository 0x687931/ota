import pytest

import main


def test_load_config_formats(tmp_path):
    json_cfg = '{"owner":"O","repo":"R"}'
    toml_cfg = 'owner = "O"\nrepo = "R"\n'

    json_path = tmp_path / "cfg.json"
    toml_path = tmp_path / "cfg.toml"

    json_path.write_text(json_cfg)
    toml_path.write_text(toml_cfg)

    assert main.load_config(str(json_path))["owner"] == "O"
    assert main.load_config(str(toml_path))["repo"] == "R"

    yaml_mod = pytest.importorskip("yaml")
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text("owner: O\nrepo: R\n")
    assert main.load_config(str(yaml_path))["owner"] == "O"
