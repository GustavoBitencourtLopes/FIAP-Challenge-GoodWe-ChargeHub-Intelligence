"""
backend/main.py

Backend do ChargeGrid Intelligence.

Fluxo de onboarding:
1. /cadastro         -> cria a conta (nome, email, senha) e já loga o usuário.
2. /cadastro-veiculo -> tela de escolha: cadastrar o veículo agora ou mais tarde.
3. /painel           -> as 4 áreas do sistema.
4. /meus-veiculos, /veiculo/<id>, /veiculo/<id>/editar -> gestão do veículo.
5. /carregamento     -> simulação de uma sessão de carregamento.

A tela inicial ("/") é pública, mas verifica se existe uma sessão ativa para
trocar os botões "Entrar / Criar conta" por "Ir para o painel" (ver rota
tela_principal abaixo).

Banco de dados: SQLite local por enquanto (facilita o desenvolvimento).
A estrutura das tabelas já é compatível com database/schema.sql, que
será usado quando migrarmos para PostgreSQL.

IMPORTANTE PARA DEPLOY (Render/produção):
Em produção, quem roda esta aplicação é o gunicorn, que apenas IMPORTA
este arquivo e usa a variável `app` — o bloco `if __name__ == "__main__"`
não é executado. Por isso, a criação das tabelas (`db.create_all()`)
acontece logo abaixo, fora desse bloco.

NOTA SOBRE SESSÕES "FANTASMA": como o banco SQLite em produção é apagado
a cada novo deploy (plano gratuito do Render), é possível que o navegador
de alguém ainda tenha um cookie de sessão apontando para um usuário que
não existe mais no banco novo. Por isso, `login_required` verifica se o
usuário realmente existe no banco (não só se o id está na sessão).
"""

import os
import random
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
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

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "troque-esta-chave-antes-de-ir-para-producao")
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
    ano = db.Column(db.Integer)
    capacidade_bateria_kwh = db.Column(db.Float)
    percentual_atual = db.Column(db.Float, default=0)

    sessoes = db.relationship(
        "SessaoCarregamento", backref="veiculo", cascade="all, delete-orphan"
    )


class SessaoCarregamento(db.Model):
    __tablename__ = "sessoes_carregamento"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    veiculo_id = db.Column(db.Integer, db.ForeignKey("veiculos.id"), nullable=False)

    percentual_inicial = db.Column(db.Float)
    percentual_final = db.Column(db.Float)
    energia_kwh = db.Column(db.Float)

    tempo_carregamento_min = db.Column(db.Float)
    tempo_espera_min = db.Column(db.Float)
    tempo_total_min = db.Column(db.Float)

    nivel_demanda = db.Column(db.String(20))
    carregadores_disponiveis = db.Column(db.Integer)
    carregadores_ocupados = db.Column(db.Integer)

    tarifa_kwh = db.Column(db.Float)
    valor_total = db.Column(db.Float)
    desconto = db.Column(db.Float)
    valor_final = db.Column(db.Float)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()


# ---------------------------------------------------------------------------
# HELPER DE AUTENTICAÇÃO
# ---------------------------------------------------------------------------

def login_required(view_func):
    """Bloqueia rotas que exigem login E limpa sessões 'fantasma'."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        usuario_id = session.get("usuario_id")

        if not usuario_id or Usuario.query.get(usuario_id) is None:
            session.pop("usuario_id", None)
            return redirect(url_for("pagina_login"))

        return view_func(*args, **kwargs)

    return wrapper


def usuario_atual():
    return Usuario.query.get(session.get("usuario_id"))


def veiculo_do_usuario_ou_404(veiculo_id):
    veiculo = Veiculo.query.get_or_404(veiculo_id)
    if veiculo.usuario_id != session.get("usuario_id"):
        abort(403)
    return veiculo


# ---------------------------------------------------------------------------
# ROTAS DE PÁGINA
# ---------------------------------------------------------------------------

@app.route("/")
def tela_principal():
    # A tela inicial é pública, mas verificamos se existe uma sessão válida
    # para trocar os botões "Entrar / Criar conta" por "Ir para o painel".
    logado = usuario_atual() is not None
    return render_template("tela_principal.html", logado=logado)


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
    usuario = usuario_atual()
    return render_template(
        "painel.html",
        usuario=usuario,
        tem_veiculo=usuario.veiculo is not None,
    )


@app.route("/meus-veiculos")
@login_required
def meus_veiculos():
    usuario = usuario_atual()
    veiculos = [usuario.veiculo] if usuario.veiculo else []
    return render_template("meus_veiculos.html", veiculos=veiculos)


@app.route("/veiculo/<int:veiculo_id>")
@login_required
def veiculo_detalhe(veiculo_id):
    veiculo = veiculo_do_usuario_ou_404(veiculo_id)
    return render_template("veiculo_detalhe.html", veiculo=veiculo)


@app.route("/veiculo/<int:veiculo_id>/editar")
@login_required
def veiculo_editar_pagina(veiculo_id):
    veiculo = veiculo_do_usuario_ou_404(veiculo_id)
    return render_template("veiculo_editar.html", veiculo=veiculo)


@app.route("/carregamento")
@login_required
def pagina_carregamento():
    usuario = usuario_atual()
    if usuario.veiculo is None:
        return redirect(url_for("pagina_cadastro_veiculo"))
    return render_template("carregamento.html", veiculo=usuario.veiculo)


# ---------------------------------------------------------------------------
# API - CADASTRO DE CONTA
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

    session["usuario_id"] = novo_usuario.id

    return jsonify({
        "mensagem": "Conta criada com sucesso.",
        "redirect": url_for("pagina_cadastro_veiculo"),
    }), 201


# ---------------------------------------------------------------------------
# API - VEÍCULO (CRIAR / ATUALIZAR / EXCLUIR)
# ---------------------------------------------------------------------------

def _numero_ou_none(valor, conversor=float):
    try:
        return conversor(valor) if valor not in (None, "") else None
    except (TypeError, ValueError):
        return None


@app.route("/api/veiculo", methods=["POST"])
@login_required
def api_veiculo_criar():
    dados = request.get_json(silent=True) or {}
    usuario = usuario_atual()

    veiculo = usuario.veiculo or Veiculo(usuario_id=usuario.id)
    veiculo.placa = (dados.get("placa") or "").strip() or None
    veiculo.marca = (dados.get("marca") or "").strip() or None
    veiculo.modelo = (dados.get("modelo") or "").strip() or None
    veiculo.ano = _numero_ou_none(dados.get("ano"), int)

    db.session.add(veiculo)
    db.session.commit()

    return jsonify({
        "mensagem": "Veículo cadastrado com sucesso.",
        "redirect": url_for("painel"),
    }), 201


@app.route("/api/veiculo/<int:veiculo_id>", methods=["POST"])
@login_required
def api_veiculo_atualizar(veiculo_id):
    veiculo = veiculo_do_usuario_ou_404(veiculo_id)
    dados = request.get_json(silent=True) or {}

    veiculo.placa = (dados.get("placa") or "").strip() or None
    veiculo.marca = (dados.get("marca") or "").strip() or None
    veiculo.modelo = (dados.get("modelo") or "").strip() or None
    veiculo.ano = _numero_ou_none(dados.get("ano"), int)

    db.session.commit()

    return jsonify({
        "mensagem": "Veículo atualizado com sucesso.",
        "redirect": url_for("veiculo_detalhe", veiculo_id=veiculo.id),
    })


@app.route("/api/veiculo/<int:veiculo_id>/excluir", methods=["POST"])
@login_required
def api_veiculo_excluir(veiculo_id):
    veiculo = veiculo_do_usuario_ou_404(veiculo_id)

    db.session.delete(veiculo)
    db.session.commit()

    return jsonify({
        "mensagem": "Veículo removido com sucesso.",
        "redirect": url_for("painel"),
    })


# ---------------------------------------------------------------------------
# API - SIMULAÇÃO DE CARREGAMENTO
# ---------------------------------------------------------------------------

TOTAL_CARREGADORES = 4
POTENCIA_CARREGADOR_KW = 7.0

TARIFAS_POR_KWH = {"baixa": 0.79, "media": 0.99, "alta": 1.29}
ESPERA_MINUTOS = {"baixa": 0, "media": 8, "alta": 18}
DESCONTO_PERCENTUAL = {"baixa": 0.10, "media": 0.0, "alta": 0.0}

RECOMENDACOES = {
    "baixa": "Baixa demanda agora — ótimo momento para carregar, com tarifa reduzida e desconto aplicado.",
    "media": "Demanda moderada no momento. Uma pequena espera pode ser necessária antes de iniciar.",
    "alta": "Alta demanda agora. Se possível, considere carregar em outro horário para economizar e evitar espera.",
}


def _simular_ocupacao(horario):
    if horario == "pico":
        pesos = [0.15, 0.35, 0.50]
    else:
        pesos = [0.55, 0.30, 0.15]

    status_possiveis = ["disponivel", "carregando", "ocupado"]
    sorteio = random.choices(status_possiveis, weights=pesos, k=TOTAL_CARREGADORES)

    disponiveis = sorteio.count("disponivel")
    ocupados = TOTAL_CARREGADORES - disponiveis
    taxa_ocupacao = ocupados / TOTAL_CARREGADORES

    if taxa_ocupacao <= 0.25:
        nivel = "baixa"
    elif taxa_ocupacao <= 0.75:
        nivel = "media"
    else:
        nivel = "alta"

    return disponiveis, ocupados, nivel, sorteio


@app.route("/api/carregamento", methods=["POST"])
@login_required
def api_carregamento_simular():
    usuario = usuario_atual()
    veiculo = usuario.veiculo

    if veiculo is None:
        return jsonify({"erro": "Cadastre um veículo antes de simular o carregamento."}), 400

    dados = request.get_json(silent=True) or {}

    percentual_atual = _numero_ou_none(dados.get("percentual_atual"))
    percentual_desejado = _numero_ou_none(dados.get("percentual_desejado"))
    capacidade_kwh = _numero_ou_none(dados.get("capacidade_bateria_kwh"))
    horario = dados.get("horario") or "normal"

    if percentual_atual is None or percentual_desejado is None or capacidade_kwh is None:
        return jsonify({"erro": "Preencha percentual atual, percentual desejado e capacidade da bateria."}), 400

    if not (0 <= percentual_atual <= 100) or not (0 <= percentual_desejado <= 100):
        return jsonify({"erro": "Percentuais devem estar entre 0 e 100."}), 400

    if percentual_desejado <= percentual_atual:
        return jsonify({"erro": "O percentual desejado deve ser maior que o atual."}), 400

    disponiveis, ocupados, nivel, status_carregadores = _simular_ocupacao(horario)

    energia_kwh = capacidade_kwh * (percentual_desejado - percentual_atual) / 100
    tempo_carregamento_min = (energia_kwh / POTENCIA_CARREGADOR_KW) * 60
    tempo_espera_min = ESPERA_MINUTOS[nivel]
    tempo_total_min = tempo_carregamento_min + tempo_espera_min

    tarifa_kwh = TARIFAS_POR_KWH[nivel]
    valor_total = energia_kwh * tarifa_kwh
    desconto = valor_total * DESCONTO_PERCENTUAL[nivel]
    valor_final = valor_total - desconto

    veiculo.capacidade_bateria_kwh = capacidade_kwh
    veiculo.percentual_atual = percentual_desejado

    sessao = SessaoCarregamento(
        usuario_id=usuario.id,
        veiculo_id=veiculo.id,
        percentual_inicial=percentual_atual,
        percentual_final=percentual_desejado,
        energia_kwh=round(energia_kwh, 2),
        tempo_carregamento_min=round(tempo_carregamento_min, 1),
        tempo_espera_min=tempo_espera_min,
        tempo_total_min=round(tempo_total_min, 1),
        nivel_demanda=nivel,
        carregadores_disponiveis=disponiveis,
        carregadores_ocupados=ocupados,
        tarifa_kwh=tarifa_kwh,
        valor_total=round(valor_total, 2),
        desconto=round(desconto, 2),
        valor_final=round(valor_final, 2),
    )
    db.session.add(sessao)
    db.session.commit()

    nivel_label = {"baixa": "Baixa", "media": "Média", "alta": "Alta"}[nivel]

    return jsonify({
        "nivel_demanda": nivel_label,
        "nivel_demanda_classe": nivel,
        "carregadores_disponiveis": disponiveis,
        "carregadores_ocupados": ocupados,
        "status_carregadores": status_carregadores,
        "energia_kwh": round(energia_kwh, 2),
        "tempo_carregamento_min": round(tempo_carregamento_min, 1),
        "tempo_espera_min": tempo_espera_min,
        "tempo_total_min": round(tempo_total_min, 1),
        "tarifa_kwh": tarifa_kwh,
        "valor_total": round(valor_total, 2),
        "desconto": round(desconto, 2),
        "valor_final": round(valor_final, 2),
        "recomendacao": RECOMENDACOES[nivel],
    })


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
    app.run(debug=True)