"""
backend/main.py

Backend inicial do ChargeGrid Intelligence.

Responsabilidades desta etapa:
- Servir as páginas do frontend (tela inicial, login, cadastro).
- Expor uma API para cadastro e login de usuários.
- Persistir usuário + veículo em banco de dados (SQLite por enquanto;
  a estrutura das tabelas já é compatível com o schema base em
  database/schema.sql, que será usado quando migrarmos para PostgreSQL).
"""

import os
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


@app.route("/dashboard")
def dashboard():
    if "usuario_id" not in session:
        return redirect(url_for("pagina_login"))
    usuario = Usuario.query.get(session["usuario_id"])
    return (
        f"<h1 style='color:#f1f5f9;background:#0b1120;padding:40px;font-family:sans-serif'>"
        f"Bem-vindo, {usuario.nome}! O dashboard ainda será construído nas próximas etapas."
        f"</h1>"
    )


# ---------------------------------------------------------------------------
# API - CADASTRO
# ---------------------------------------------------------------------------

@app.route("/api/cadastro", methods=["POST"])
def api_cadastro():
    dados = request.get_json(silent=True) or {}

    nome = (dados.get("nome") or "").strip()
    email = (dados.get("email") or "").strip().lower()
    senha = dados.get("senha") or ""

    placa = (dados.get("placa") or "").strip()
    marca = (dados.get("marca") or "").strip()
    modelo = (dados.get("modelo") or "").strip()
    capacidade = dados.get("capacidade_bateria_kwh")
    percentual_atual = dados.get("percentual_atual")

    if not nome or not email or not senha:
        return jsonify({"erro": "Nome, email e senha são obrigatórios."}), 400

    if Usuario.query.filter_by(email=email).first():
        return jsonify({"erro": "Já existe uma conta com este email."}), 409

    novo_usuario = Usuario(nome=nome, email=email)
    novo_usuario.set_senha(senha)
    db.session.add(novo_usuario)
    db.session.flush()  # garante que novo_usuario.id já existe antes de criar o veículo

    novo_veiculo = Veiculo(
        usuario_id=novo_usuario.id,
        placa=placa or None,
        marca=marca or None,
        modelo=modelo or None,
        capacidade_bateria_kwh=float(capacidade) if capacidade else None,
        percentual_atual=float(percentual_atual) if percentual_atual else 0,
    )
    db.session.add(novo_veiculo)
    db.session.commit()

    return jsonify({"mensagem": "Conta criada com sucesso."}), 201


# ---------------------------------------------------------------------------
# API - LOGIN
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
    return jsonify({"mensagem": "Login realizado com sucesso.", "redirect": url_for("dashboard")})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("usuario_id", None)
    return jsonify({"mensagem": "Logout realizado."})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)