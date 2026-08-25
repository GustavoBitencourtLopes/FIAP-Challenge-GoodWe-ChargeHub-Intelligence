"""
backend/main.py

Backend do ChargeGrid Intelligence.

Fluxo de onboarding:
1. /cadastro         -> cria a conta (nome, email, senha) e já loga o usuário.
2. /cadastro-veiculo -> tela de escolha: cadastrar o veículo agora ou mais tarde.
3. /painel           -> as áreas do sistema.
4. /meus-veiculos, /veiculo/<id>, /veiculo/<id>/editar -> gestão do veículo.
5. /carregamento     -> simulação inteligente de uma sessão de carregamento,
   com pagamento simulado (escolha de forma de pagamento).
6. /minha-conta      -> visualizar dados da conta e trocar senha.
7. /historico        -> histórico de sessões de carregamento do usuário.

SISTEMA INTELIGENTE (regras, sem custo de API):
Decide se é horário de pico, a lotação do outlet, sugestões de horário,
dicas de cupom e o preço final (com taxa extra em horário de pico).

CHATBOT (IA generativa real, via API da Anthropic):
Diferente do sistema de regras acima, o assistente de chat (/api/chat) usa
de fato o modelo Claude Haiku 4.5 para responder perguntas livres do
usuário. Isso tem um custo real, porém muito baixo (Haiku é o modelo mais
barato da Anthropic). A chave de API fica em uma variável de ambiente
(ANTHROPIC_API_KEY), nunca no código-fonte.

Banco de dados: SQLite local por enquanto (facilita o desenvolvimento).
A estrutura das tabelas já é compatível com database/schema.sql, que
será usado quando migrarmos para PostgreSQL.

IMPORTANTE PARA DEPLOY (Render/produção):
Em produção, quem roda esta aplicação é o gunicorn, que apenas IMPORTA
este arquivo e usa a variável `app` — o bloco `if __name__ == "__main__"`
não é executado. Por isso, a criação das tabelas (`db.create_all()`)
acontece logo abaixo, fora desse bloco. A variável ANTHROPIC_API_KEY
precisa ser configurada nas variáveis de ambiente do Render também
(Settings -> Environment), não só no .env local.

NOTA SOBRE FUSO HORÁRIO: o horário de pico é calculado usando o fuso de
São Paulo. Em algumas máquinas Windows, o Python não tem o banco de dados
de fusos horários (IANA) instalado por padrão — por isso o pacote
"tzdata" está no requirements.txt, com um fallback de segurança no código.
"""

import os
import random
from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()  # lê o arquivo .env local, se existir (não afeta produção no Render)

try:
    import anthropic
except ImportError:
    anthropic = None

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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
anthropic_client = None
if anthropic is not None and ANTHROPIC_API_KEY:
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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

    nivel_lotacao = db.Column(db.String(20))
    postos_operacionais = db.Column(db.Integer)
    postos_disponiveis = db.Column(db.Integer)
    postos_ocupados = db.Column(db.Integer)
    horario_pico = db.Column(db.Boolean, default=False)

    tarifa_base_kwh = db.Column(db.Float)
    taxa_pico_kwh = db.Column(db.Float)
    valor_total = db.Column(db.Float)
    desconto = db.Column(db.Float)
    valor_final = db.Column(db.Float)

    pago = db.Column(db.Boolean, default=False)
    forma_pagamento = db.Column(db.String(30))
    pago_em = db.Column(db.DateTime)

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


@app.route("/minha-conta")
@login_required
def pagina_minha_conta():
    usuario = usuario_atual()
    return render_template("minha_conta.html", usuario=usuario)


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
    veiculo = usuario.veiculo

    if veiculo is None:
        return redirect(url_for("pagina_cadastro_veiculo"))

    capacidade_definida = veiculo.capacidade_bateria_kwh is not None
    return render_template(
        "carregamento.html",
        veiculo=veiculo,
        capacidade_definida=capacidade_definida,
    )


FORMA_PAGAMENTO_LABEL = {
    "cartao_credito": "Cartão de Crédito",
    "cartao_debito": "Cartão de Débito",
    "pix": "Pix",
    "carteira_digital": "Carteira Digital",
}


def _formatar_data_br(dt_utc):
    if dt_utc is None:
        return "—"
    try:
        dt_com_tz = dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Sao_Paulo"))
    except ZoneInfoNotFoundError:
        dt_com_tz = dt_utc
    return dt_com_tz.strftime("%d/%m/%Y às %H:%M")


@app.route("/historico")
@login_required
def historico():
    usuario = usuario_atual()
    sessoes_db = (
        SessaoCarregamento.query
        .filter_by(usuario_id=usuario.id)
        .order_by(SessaoCarregamento.criado_em.desc())
        .all()
    )

    sessoes = [
        {
            "data": _formatar_data_br(s.criado_em),
            "percentual_inicial": s.percentual_inicial,
            "percentual_final": s.percentual_final,
            "energia_kwh": s.energia_kwh,
            "valor_final": s.valor_final,
            "pago": s.pago,
            "forma_pagamento": FORMA_PAGAMENTO_LABEL.get(s.forma_pagamento, "—"),
        }
        for s in sessoes_db
    ]

    return render_template("historico.html", sessoes=sessoes)


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
    veiculo.capacidade_bateria_kwh = _numero_ou_none(dados.get("capacidade_bateria_kwh"))

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
    veiculo.capacidade_bateria_kwh = _numero_ou_none(dados.get("capacidade_bateria_kwh"))

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
# "IA" — SISTEMA INTELIGENTE DE CARREGAMENTO (baseado em regras)
# ---------------------------------------------------------------------------

TOTAL_CARREGADORES = 4
POTENCIA_CARREGADOR_KW = 7.0  # mesma potência do GW7K-HCA-20 (linha HCA G2 da GoodWe)

HORARIOS_PICO = [(8, 10), (18, 21)]  # fuso de São Paulo

TARIFAS_POR_KWH = {"baixa": 0.79, "media": 0.99, "alta": 1.29}
ESPERA_MINUTOS = {"baixa": 0, "media": 8, "alta": 18}
DESCONTO_PERCENTUAL = {"baixa": 0.10, "media": 0.0, "alta": 0.0}
TAXA_PICO_PERCENTUAL = 0.15

LOTACAO_LABEL = {"baixa": "Tranquilo", "media": "Moderado", "alta": "Lotado"}

RECOMENDACOES = {
    "baixa": "Poucos veículos no outlet agora — ótimo momento para carregar sem espera.",
    "media": "Movimento moderado no outlet. Uma pequena espera pode ocorrer antes de iniciar.",
    "alta": "O outlet está bastante cheio agora. Se puder, considere voltar em outro horário.",
}

CUPONS = [
    "15% de desconto na Cafeteria do Outlet",
    "Cashback de R$ 10 em qualquer loja parceira",
    "Compre 1 e leve 2 na loja de conveniência",
    "10% de desconto na praça de alimentação",
    "Frete grátis em compras acima de R$ 100 na loja âncora",
]

FORMAS_PAGAMENTO_VALIDAS = set(FORMA_PAGAMENTO_LABEL.keys())


def _agora_sao_paulo():
    try:
        return datetime.now(ZoneInfo("America/Sao_Paulo"))
    except ZoneInfoNotFoundError:
        return datetime.now()


def _esta_em_horario_de_pico(agora=None):
    agora = agora or _agora_sao_paulo()
    hora = agora.hour
    return any(inicio <= hora < fim for inicio, fim in HORARIOS_PICO)


def _simular_postos(pico):
    em_manutencao = 1 if random.random() < 0.12 else 0
    operacionais = TOTAL_CARREGADORES - em_manutencao

    pesos = [0.15, 0.35, 0.50] if pico else [0.55, 0.30, 0.15]
    status_possiveis = ["disponivel", "carregando", "ocupado"]
    sorteio = random.choices(status_possiveis, weights=pesos, k=operacionais)

    disponiveis = sorteio.count("disponivel")
    ocupados = operacionais - disponiveis
    taxa_ocupacao = ocupados / operacionais if operacionais else 1

    if taxa_ocupacao <= 0.25:
        nivel = "baixa"
    elif taxa_ocupacao <= 0.75:
        nivel = "media"
    else:
        nivel = "alta"

    return {
        "em_manutencao": em_manutencao,
        "operacionais": operacionais,
        "disponiveis": disponiveis,
        "ocupados": ocupados,
        "nivel": nivel,
        "status_lista": sorteio,
    }


def _gerar_sugestao_horario(ocupados, nivel):
    if ocupados == 0:
        return "Não há espera no momento — você pode carregar agora mesmo."
    if nivel == "baixa":
        return "Poucos veículos aguardando. Você deve conseguir uma vaga rapidamente."
    minutos = random.choice([15, 20, 25, 30, 40])
    liberam = random.randint(1, ocupados)
    return f"Daqui a {minutos} minutos, aproximadamente {liberam} posto(s) devem ficar livres."


@app.route("/api/carregamento", methods=["POST"])
@login_required
def api_carregamento_simular():
    usuario = usuario_atual()
    veiculo = usuario.veiculo

    if veiculo is None:
        return jsonify({"erro": "Cadastre um veículo antes de simular o carregamento."}), 400

    if veiculo.capacidade_bateria_kwh is None:
        return jsonify({
            "erro": "Complete o cadastro do seu veículo com a capacidade da bateria antes de simular.",
        }), 400

    dados = request.get_json(silent=True) or {}

    percentual_atual = _numero_ou_none(dados.get("percentual_atual"))
    percentual_desejado = _numero_ou_none(dados.get("percentual_desejado"))

    if percentual_atual is None or percentual_desejado is None:
        return jsonify({"erro": "Preencha o percentual atual e o percentual desejado."}), 400

    if not (0 <= percentual_atual <= 100) or not (0 <= percentual_desejado <= 100):
        return jsonify({"erro": "Percentuais devem estar entre 0 e 100."}), 400

    if percentual_desejado <= percentual_atual:
        return jsonify({"erro": "O percentual desejado deve ser maior que o atual."}), 400

    capacidade_kwh = veiculo.capacidade_bateria_kwh
    pico = _esta_em_horario_de_pico()
    postos = _simular_postos(pico)
    nivel = postos["nivel"]

    energia_kwh = capacidade_kwh * (percentual_desejado - percentual_atual) / 100
    tempo_carregamento_min = (energia_kwh / POTENCIA_CARREGADOR_KW) * 60
    tempo_espera_min = ESPERA_MINUTOS[nivel]
    tempo_total_min = tempo_carregamento_min + tempo_espera_min

    tarifa_base = TARIFAS_POR_KWH[nivel]
    taxa_pico_kwh = tarifa_base * TAXA_PICO_PERCENTUAL if pico else 0.0
    tarifa_final_kwh = tarifa_base + taxa_pico_kwh

    valor_total = energia_kwh * tarifa_final_kwh
    desconto = valor_total * DESCONTO_PERCENTUAL[nivel]
    valor_final = valor_total - desconto

    # NOTA: não persistimos mais o percentual_atual do veículo aqui — cada
    # simulação começa do zero, o usuário informa livremente o ponto de
    # partida da bateria a cada nova sessão.

    sessao = SessaoCarregamento(
        usuario_id=usuario.id,
        veiculo_id=veiculo.id,
        percentual_inicial=percentual_atual,
        percentual_final=percentual_desejado,
        energia_kwh=round(energia_kwh, 2),
        tempo_carregamento_min=round(tempo_carregamento_min, 1),
        tempo_espera_min=tempo_espera_min,
        tempo_total_min=round(tempo_total_min, 1),
        nivel_lotacao=nivel,
        postos_operacionais=postos["operacionais"],
        postos_disponiveis=postos["disponiveis"],
        postos_ocupados=postos["ocupados"],
        horario_pico=pico,
        tarifa_base_kwh=tarifa_base,
        taxa_pico_kwh=round(taxa_pico_kwh, 4),
        valor_total=round(valor_total, 2),
        desconto=round(desconto, 2),
        valor_final=round(valor_final, 2),
    )
    db.session.add(sessao)
    db.session.commit()

    recomendacao = RECOMENDACOES[nivel]
    if pico:
        recomendacao += " Como é horário de pico, uma taxa adicional foi aplicada à tarifa."

    return jsonify({
        "sessao_id": sessao.id,

        "lotacao_label": LOTACAO_LABEL[nivel],
        "lotacao_classe": nivel,
        "horario_pico": pico,

        "postos_total": TOTAL_CARREGADORES,
        "postos_operacionais": postos["operacionais"],
        "postos_em_manutencao": postos["em_manutencao"],
        "postos_disponiveis": postos["disponiveis"],
        "postos_ocupados": postos["ocupados"],
        "status_postos": postos["status_lista"],

        "energia_kwh": round(energia_kwh, 2),
        "tempo_carregamento_min": round(tempo_carregamento_min, 1),
        "tempo_espera_min": tempo_espera_min,
        "tempo_total_min": round(tempo_total_min, 1),

        "tarifa_base_kwh": tarifa_base,
        "taxa_pico_kwh": round(taxa_pico_kwh, 4),
        "valor_total": round(valor_total, 2),
        "desconto": round(desconto, 2),
        "valor_final": round(valor_final, 2),

        "recomendacao": recomendacao,
        "sugestao_horario": _gerar_sugestao_horario(postos["ocupados"], nivel),
        "dica_cupom": random.choice(CUPONS),
    })


@app.route("/api/carregamento/<int:sessao_id>/pagar", methods=["POST"])
@login_required
def api_carregamento_pagar(sessao_id):
    sessao = SessaoCarregamento.query.get_or_404(sessao_id)
    if sessao.usuario_id != session.get("usuario_id"):
        abort(403)

    dados = request.get_json(silent=True) or {}
    forma_pagamento = dados.get("forma_pagamento")

    if forma_pagamento not in FORMAS_PAGAMENTO_VALIDAS:
        return jsonify({"erro": "Selecione uma forma de pagamento válida."}), 400

    sessao.pago = True
    sessao.forma_pagamento = forma_pagamento
    sessao.pago_em = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "mensagem": "Pagamento aprovado com sucesso!",
        "forma_pagamento_label": FORMA_PAGAMENTO_LABEL[forma_pagamento],
    })


# ---------------------------------------------------------------------------
# API - CHATBOT (IA generativa real, via Anthropic)
# ---------------------------------------------------------------------------

CHAT_MODELO = "claude-haiku-4-5-20251001"  # modelo mais barato da Anthropic

CHAT_SYSTEM_PROMPT = """Você é o assistente virtual do ChargeGrid Intelligence, uma plataforma que
simula uma rede de carregadores de veículos elétricos em outlets comerciais (projeto do FIAP EV
Challenge 2026, em parceria com a GoodWe).

Ajude o usuário com:
- dúvidas sobre como funciona o carregamento do veículo elétrico dele;
- recomendações gerais sobre horários de menor demanda no outlet;
- dúvidas sobre a plataforma (cadastro de veículo, histórico de sessões, pagamento simulado);
- curiosidades gerais sobre carros elétricos e recarga.

Seja breve, direto e simpático. Respostas de no máximo 3 a 4 frases. Se não souber algo específico
sobre a sessão atual do usuário, seja honesto e sugira que ele confira a tela de "Fazer carregamento"
ou o "Histórico" no painel."""


@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    if anthropic_client is None:
        return jsonify({
            "erro": "O assistente ainda não foi configurado. Peça para o administrador definir a ANTHROPIC_API_KEY.",
        }), 503

    dados = request.get_json(silent=True) or {}
    mensagens = dados.get("mensagens") or []

    if not mensagens:
        return jsonify({"erro": "Envie uma mensagem."}), 400

    usuario = usuario_atual()
    contexto_pessoal = f" O usuário logado se chama {usuario.nome}."
    if usuario.veiculo:
        marca = usuario.veiculo.marca or ""
        modelo = usuario.veiculo.modelo or ""
        if marca or modelo:
            contexto_pessoal += f" O veículo cadastrado dele é um {marca} {modelo}.".strip()

    try:
        resposta = anthropic_client.messages.create(
            model=CHAT_MODELO,
            max_tokens=300,
            system=CHAT_SYSTEM_PROMPT + contexto_pessoal,
            messages=mensagens[-10:],  # limita o histórico enviado, controla custo
        )
        texto_resposta = resposta.content[0].text
        return jsonify({"resposta": texto_resposta})
    except Exception:
        return jsonify({"erro": "Não foi possível falar com o assistente agora. Tente novamente em instantes."}), 502


# ---------------------------------------------------------------------------
# API - CONTA (TROCA DE SENHA)
# ---------------------------------------------------------------------------

@app.route("/api/conta/senha", methods=["POST"])
@login_required
def api_conta_alterar_senha():
    usuario = usuario_atual()
    dados = request.get_json(silent=True) or {}

    senha_atual = dados.get("senha_atual") or ""
    nova_senha = dados.get("nova_senha") or ""

    if not senha_atual or not nova_senha:
        return jsonify({"erro": "Informe a senha atual e a nova senha."}), 400

    if len(nova_senha) < 6:
        return jsonify({"erro": "A nova senha precisa ter pelo menos 6 caracteres."}), 400

    if not usuario.checar_senha(senha_atual):
        return jsonify({"erro": "Senha atual incorreta."}), 401

    usuario.set_senha(nova_senha)
    db.session.commit()

    return jsonify({"mensagem": "Senha alterada com sucesso."})


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