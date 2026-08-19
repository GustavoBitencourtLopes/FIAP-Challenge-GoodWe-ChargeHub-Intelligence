"""
backend/main.py

Backend do ChargeGrid Intelligence.

Fluxo de onboarding em duas etapas:
1. /cadastro       -> cria a conta (nome, email, senha) e já loga o usuário.
2. /cadastro-veiculo -> usuário logado cadastra o veículo (etapa separada).
Depois disso, o usuário cai no /painel, com as 4 áreas do sistema.

Banco de dados: SQLite local por enquanto (facilita o desenvolvimento).
A estrutura das tabelas já é compatível com database/schema.sql, que
será usado quando migrarmos para PostgreSQL.
"""

import os
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

app = Flask(
    __name__,
    template_folder=FRONTEND_DIR,
    static_folder=os.path.join(FRONTEND_DIR, "static"),
    static_url_path="/static",
)

app.config["SECRET_KEY"] = "troque-esta-chave-antes-de-ir-para-producao"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "chargegrid.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ---------------------------------------------------------------------------
# MODELOS
# ---------------------------------------------------------------------------

class Usuario(db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)

    veiculo = db.relationship(
        "Veiculo", backref="usuario", uselist=False, cascade="all, delete-orphan"
    )

    def set_senha(self, senha_plana):
        self.senha_hash = generate_password_hash(senha_plana)

    def checar_senha(self, senha_plana):
        return check_password_hash(self.senha_hash, senha_plana)


class Veiculo(db.Model):
    __tablename__ = "veiculos"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    placa = db.Column(db.String(10))
    marca = db.Column(db.String(60))
    modelo = db.Column(db.String(60))
    capacidade_bateria_kwh = db.Column(db.Float)
    percentual_atual = db.Column(db.Float, default=0)


# ---------------------------------------------------------------------------
# HELPER DE AUTENTICAÇÃO
# ---------------------------------------------------------------------------

def login_required(view_func):
    """Bloqueia o acesso a rotas que exigem usuário logado."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("pagina_login"))
        return view_func(*args, **kwargs)

    return wrapper


def usuario_atual():
    return Usuario.query.get(session.get("usuario_id"))


# ---------------------------------------------------------------------------
# ROTAS DE PÁGINA
# ---------------------------------------------------------------------------

@app.route("/")
def tela_principal():
    return render_template("tela_principal.html")


@app.route("/login")
def pagina_login():
    return render_template("login.html")


@app.route("/cadastro")
def pagina_cadastro():
    return render_template("cadastro.html")


@app.route("/cadastro-veiculo")
@login_required
def pagina_cadastro_veiculo():
    return render_template("cadastro_veiculo.html")


@app.route("/painel")
@login_required
def painel():
    return render_template("painel.html", usuario=usuario_atual())


# ---------------------------------------------------------------------------
# API - CADASTRO DE CONTA (ETAPA 1)
# ---------------------------------------------------------------------------

@app.route("/api/cadastro", methods=["POST"])
def api_cadastro():
    dados = request.get_json(silent=True) or {}

    nome = (dados.get("nome") or "").strip()
    email = (dados.get("email") or "").strip().lower()
    senha = dados.get("senha") or ""

    if not nome or not email or not senha:
        return jsonify({"erro": "Nome, email e senha são obrigatórios."}), 400

    if Usuario.query.filter_by(email=email).first():
        return jsonify({"erro": "Já existe uma conta com este email."}), 409

    novo_usuario = Usuario(nome=nome, email=email)
    novo_usuario.set_senha(senha)
    db.session.add(novo_usuario)
    db.session.commit()

    # Já loga o usuário para seguir direto para a etapa 2 (cadastro do veículo).
    session["usuario_id"] = novo_usuario.id

    return jsonify({
        "mensagem": "Conta criada com sucesso.",
        "redirect": url_for("pagina_cadastro_veiculo"),
    }), 201


# ---------------------------------------------------------------------------
# API - CADASTRO DE VEÍCULO (ETAPA 2)
# ---------------------------------------------------------------------------

@app.route("/api/veiculo", methods=["POST"])
@login_required
def api_veiculo():
    dados = request.get_json(silent=True) or {}
    usuario = usuario_atual()

    placa = (dados.get("placa") or "").strip()
    marca = (dados.get("marca") or "").strip()
    modelo = (dados.get("modelo") or "").strip()
    capacidade = dados.get("capacidade_bateria_kwh")
    percentual_atual = dados.get("percentual_atual")

    veiculo = usuario.veiculo or Veiculo(usuario_id=usuario.id)
    veiculo.placa = placa or None
    veiculo.marca = marca or None
    veiculo.modelo = modelo or None
    veiculo.capacidade_bateria_kwh = float(capacidade) if capacidade else None
    veiculo.percentual_atual = float(percentual_atual) if percentual_atual else 0

    db.session.add(veiculo)
    db.session.commit()

    return jsonify({
        "mensagem": "Veículo cadastrado com sucesso.",
        "redirect": url_for("painel"),
    }), 201


# ---------------------------------------------------------------------------
# API - LOGIN / LOGOUT
# ---------------------------------------------------------------------------

@app.route("/api/login", methods=["POST"])
def api_login():
    dados = request.get_json(silent=True) or {}

    email = (dados.get("email") or "").strip().lower()
    senha = dados.get("senha") or ""

    usuario = Usuario.query.filter_by(email=email).first()

    if not usuario or not usuario.checar_senha(senha):
        return jsonify({"erro": "Email ou senha inválidos."}), 401

    session["usuario_id"] = usuario.id
    return jsonify({"mensagem": "Login realizado com sucesso.", "redirect": url_for("painel")})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("usuario_id", None)
    return jsonify({"mensagem": "Logout realizado."})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)