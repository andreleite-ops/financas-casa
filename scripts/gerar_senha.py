"""Gera os hashes bcrypt das senhas do Andre e da Ro.

    python scripts/gerar_senha.py

Cole a saida em .streamlit/secrets.toml (ou em Settings > Secrets no Streamlit
Cloud). A senha em si nunca e gravada em lugar nenhum.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt  # noqa: E402


def gerar(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def main() -> None:
    print("Gerador de senhas — Finanças da Casa\n")
    blocos = []
    for login, nome in (("andre", "André"), ("ro", "Rô")):
        while True:
            senha = getpass.getpass(f"Senha de {nome}: ")
            if len(senha) < 6:
                print("  Use pelo menos 6 caracteres.")
                continue
            if senha != getpass.getpass(f"Repita a senha de {nome}: "):
                print("  As senhas não conferem. Tente de novo.")
                continue
            break
        blocos.append(f'[usuarios.{login}]\nnome = "{nome}"\nsenha_hash = "{gerar(senha)}"')

    print("\n" + "=" * 62)
    print("Cole este bloco em .streamlit/secrets.toml:\n")
    print("\n\n".join(blocos))
    print("=" * 62)


if __name__ == "__main__":
    main()
