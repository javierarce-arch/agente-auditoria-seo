import base64

from auditor_seo import correo


def test_xoauth2_arma_el_payload_base64():
    resultado = correo._xoauth2("bot@utn.test", "token123")

    decodificado = base64.b64decode(resultado).decode()
    assert decodificado == "user=bot@utn.test\x01auth=Bearer token123\x01\x01"


def test_credenciales_disponibles_true_si_existe_el_archivo(tmp_path):
    archivo = tmp_path / "token.json"
    archivo.write_text("{}")

    assert correo.credenciales_disponibles(str(archivo)) is True


def test_credenciales_disponibles_false_si_no_existe(tmp_path):
    assert correo.credenciales_disponibles(str(tmp_path / "no-existe.json")) is False


def test_credenciales_disponibles_usa_gmail_token_file(monkeypatch, tmp_path):
    archivo = tmp_path / "token.json"
    archivo.write_text("{}")
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(archivo))

    assert correo.credenciales_disponibles() is True
