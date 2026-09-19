"""
backend/main.py

Backend do ChargeGrid Intelligence.

Fluxo de onboarding:
1. /cadastro         -> cria a conta (nome, email, senha) e já loga o usuário.
2. /cadastro-veiculo -> tela de escolha: cadastrar o veículo agora ou mais tarde.
3. /painel           -> as áreas do sistema (cliente).
4. /meus-veiculos, /veiculo/<id>, /veiculo/<id>/editar -> gestão do veículo.
5. /carregamento     -> o usuário ESCOLHE manualmente qual posto usar (o
   sistema mostra o status ao vivo dos 4 postos) e simula a sessão.
6. /minha-conta      -> visualizar dados da conta, cupons resgatados e
   trocar senha.
7. /historico        -> histórico de sessões de carregamento do usuário.
8. /relatorios-ia    -> assistente virtual (chat) via Google Gemini.
9. /vip              -> assinatura VIP, que libera o posto DC Rápido 60kW.
10. /login-gestor    -> tela de login separada para a conta de gestor
    (usuário "gestor", senha "123") — acessível pelo link "Login corporativo"
    na tela de login normal.
11. /gestor/painel   -> painel do gestor do outlet (só para contas do tipo
    "gestor"): KPIs, demanda, tarifação, faturamento, cupons, simulador e
    um assistente de IA que já conhece os dados reais da operação.

SISTEMA INTELIGENTE (regras, sem custo de API):
- Detecta horário de pico pelo relógio real (fuso de São Paulo).
- 4 postos de carregamento HETEROGÊNEOS (2x AC 7kW, 1x AC 22kW,
  1x DC Rápido 60kW — este último exclusivo para assinantes VIP).
- O usuário escolhe qual posto usar entre os disponíveis no momento.
- Calcula o tempo de carga respeitando a curva de carregamento real de
  baterias de íon-lítio (rápido até 80%, mais lento depois).
- Gera de 2 a 3 ofertas comerciais de lojas do outlet a cada sessão, que
  o usuário pode resgatar (fica salvo em "Meus cupons", na tela de conta).

Banco de dados: SQLite local por enquanto (facilita o desenvolvimento).
A estrutura das tabelas já é compatível com database/schema.sql, que
será usado quando migrarmos para PostgreSQL.

IMPORTANTE PARA DEPLOY (Render/produção):
Em produção, quem roda esta aplicação é o gunicorn, que apenas IMPORTA
este arquivo e usa a variável `app` — o bloco `if __name__ == "__main__"`
não é executado. A criação das tabelas (`db.create_all()`) acontece fora
desse bloco. A variável GOOGLE_API_KEY precisa ser configurada nas
variáveis de ambiente do Render também (Settings -> Environment).

NOTA SOBRE FUSO HORÁRIO: o horário de pico é calculado usando o fuso de
São Paulo, com fallback de segurança caso o "tzdata" não esteja
disponível na máquina.
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

load_dotenv()

try:
    from google import genai
except ImportError:
    genai = None

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

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
gemini_client = None
if genai is not None and GOOGLE_API_KEY:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

# ---------------------------------------------------------------------------
# AVISO PERMANENTE DE STATUS DA IA (aparece toda vez que o servidor liga)
# ---------------------------------------------------------------------------

print("=" * 60)
if genai is None:
    print("STATUS DA IA: ❌ pacote 'google-genai' não instalado.")
    print("  -> Rode: pip install -r backend/requirements.txt")
elif not GOOGLE_API_KEY:
    print("STATUS DA IA: ❌ GOOGLE_API_KEY não encontrada no .env.")
else:
    print("STATUS DA IA: ✅ configurada e pronta para uso (Gemini).")
print("=" * 60)


# ---------------------------------------------------------------------------
# MODELOS
# ---------------------------------------------------------------------------

class Usuario(db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    vip = db.Column(db.Boolean, default=False)
    tipo = db.Column(db.String(20), default="cliente")  # "cliente" ou "gestor"

    veiculo = db.relationship(
        "Veiculo", backref="usuario", uselist=False, cascade="all, delete-orphan"
    )
    cupons = db.relationship(
        "CupomResgatado", backref="usuario", cascade="all, delete-orphan",
        order_by="desc(CupomResgatado.criado_em)",
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

    carregador_tipo = db.Column(db.String(40))
    carregador_potencia_kw = db.Column(db.Float)

    tarifa_base_kwh = db.Column(db.Float)
    taxa_pico_kwh = db.Column(db.Float)
    valor_total = db.Column(db.Float)
    desconto = db.Column(db.Float)
    valor_final = db.Column(db.Float)

    pago = db.Column(db.Boolean, default=False)
    forma_pagamento = db.Column(db.String(30))
    pago_em = db.Column(db.DateTime)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class CupomResgatado(db.Model):
    __tablename__ = "cupons_resgatados"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    loja = db.Column(db.String(80))
    categoria = db.Column(db.String(60))
    descricao = db.Column(db.String(200))
    codigo = db.Column(db.String(20))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()

    # Conta de gestor criada automaticamente, para demonstração. Como o
    # banco no Render é recriado a cada deploy, isso garante que essa
    # conta sempre existe, sem precisar de um cadastro manual toda vez.
    if not Usuario.query.filter_by(email="gestor").first():
        gestor_demo = Usuario(nome="Gestor do Outlet", email="gestor", tipo="gestor")
        gestor_demo.set_senha("123")
        db.session.add(gestor_demo)
        db.session.commit()
        print("Conta de gestor de demonstração criada: usuário 'gestor' / senha '123'")


# ---------------------------------------------------------------------------
# HELPERS DE AUTENTICAÇÃO
# ---------------------------------------------------------------------------

def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        usuario_id = session.get("usuario_id")

        if not usuario_id or Usuario.query.get(usuario_id) is None:
            session.pop("usuario_id", None)
            return redirect(url_for("pagina_login"))

        return view_func(*args, **kwargs)

    return wrapper


def gestor_required(view_func):
    """Bloqueia rotas do painel do gestor para quem não tem conta de gestor
    (mesmo estando logado como cliente)."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        usuario_id = session.get("usuario_id")
        usuario = Usuario.query.get(usuario_id) if usuario_id else None

        if usuario is None:
            session.pop("usuario_id", None)
            return redirect(url_for("pagina_login"))

        if usuario.tipo != "gestor":
            return redirect(url_for("painel"))

        return view_func(*args, **kwargs)

    return wrapper


def usuario_atual():
    return Usuario.query.get(session.get("usuario_id"))


def veiculo_do_usuario_ou_404(veiculo_id):
    veiculo = Veiculo.query.get_or_404(veiculo_id)
    if veiculo.usuario_id != session.get("usuario_id"):
        abort(403)
    return veiculo


def _formatar_data_br(dt_utc):
    if dt_utc is None:
        return "—"
    try:
        dt_com_tz = dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Sao_Paulo"))
    except ZoneInfoNotFoundError:
        dt_com_tz = dt_utc
    return dt_com_tz.strftime("%d/%m/%Y às %H:%M")


def _hora_sao_paulo(dt_utc):
    """Converte um datetime salvo em UTC para a hora local de São Paulo
    (0-23), usada nos gráficos de demanda por horário do gestor."""
    if dt_utc is None:
        return 0
    try:
        return dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Sao_Paulo")).hour
    except ZoneInfoNotFoundError:
        return dt_utc.hour


# ---------------------------------------------------------------------------
# ROTAS DE PÁGINA — CLIENTE
# ---------------------------------------------------------------------------

@app.route("/")
def tela_principal():
    logado = usuario_atual() is not None
    return render_template("tela_principal.html", logado=logado)


@app.route("/login")
def pagina_login():
    return render_template("login.html")


@app.route("/login-gestor")
def pagina_login_gestor():
    return render_template("login_gestor.html")


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

    if usuario.tipo == "gestor":
        return redirect(url_for("painel_gestor"))

    return render_template(
        "painel.html",
        usuario=usuario,
        tem_veiculo=usuario.veiculo is not None,
        pagina_atual="painel",
    )


@app.route("/minha-conta")
@login_required
def pagina_minha_conta():
    usuario = usuario_atual()
    cupons = [
        {
            "loja": c.loja,
            "categoria": c.categoria,
            "descricao": c.descricao,
            "codigo": c.codigo,
            "data": _formatar_data_br(c.criado_em),
        }
        for c in usuario.cupons
    ]
    return render_template("minha_conta.html", usuario=usuario, cupons=cupons, pagina_atual="minha_conta")


@app.route("/meus-veiculos")
@login_required
def meus_veiculos():
    usuario = usuario_atual()
    veiculos = [usuario.veiculo] if usuario.veiculo else []
    return render_template("meus_veiculos.html", veiculos=veiculos, usuario=usuario, pagina_atual="veiculo")


@app.route("/veiculo/<int:veiculo_id>")
@login_required
def veiculo_detalhe(veiculo_id):
    veiculo = veiculo_do_usuario_ou_404(veiculo_id)
    return render_template("veiculo_detalhe.html", veiculo=veiculo, usuario=usuario_atual(), pagina_atual="veiculo")


@app.route("/veiculo/<int:veiculo_id>/editar")
@login_required
def veiculo_editar_pagina(veiculo_id):
    veiculo = veiculo_do_usuario_ou_404(veiculo_id)
    return render_template("veiculo_editar.html", veiculo=veiculo, usuario=usuario_atual(), pagina_atual="veiculo")


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
        usuario=usuario,
        capacidade_definida=capacidade_definida,
        pagina_atual="carregamento",
    )


@app.route("/relatorios-ia")
@login_required
def pagina_relatorios_ia():
    return render_template("relatorios_ia.html", usuario=usuario_atual(), pagina_atual="ia")


PRECO_VIP_MENSAL = 29.90


@app.route("/vip")
@login_required
def pagina_vip():
    usuario = usuario_atual()
    return render_template("vip.html", usuario=usuario, preco_vip=PRECO_VIP_MENSAL, pagina_atual="vip")


FORMA_PAGAMENTO_LABEL = {
    "cartao_credito": "Cartão de Crédito",
    "cartao_debito": "Cartão de Débito",
    "pix": "Pix",
    "carteira_digital": "Carteira Digital",
}


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
            "carregador_tipo": s.carregador_tipo or "—",
        }
        for s in sessoes_db
    ]

    return render_template("historico.html", sessoes=sessoes, usuario=usuario, pagina_atual="historico")


# ---------------------------------------------------------------------------
# ROTAS DE PÁGINA — GESTOR
# ---------------------------------------------------------------------------

# Horas do dia (0-23) que caem dentro do horário de pico — usado só para
# destacar visualmente o gráfico de demanda no painel do gestor. Mantido
# em sincronia manual com HORARIOS_PICO, definido mais abaixo.
HORAS_DE_PICO = {8, 9, 18, 19, 20}


def _calcular_metricas_outlet():
    """Calcula TODAS as métricas agregadas do painel do gestor num só
    lugar. Usado tanto para renderizar o painel quanto para alimentar o
    assistente de IA do gestor — assim os dois sempre mostram os mesmos
    números, sem duplicar a lógica de cálculo."""

    todas_sessoes = SessaoCarregamento.query.all()
    sessoes_pagas = [s for s in todas_sessoes if s.pago]

    # KPIs
    receita_total = sum(s.valor_final for s in sessoes_pagas)
    numero_sessoes_pagas = len(sessoes_pagas)
    ticket_medio = (receita_total / numero_sessoes_pagas) if numero_sessoes_pagas else 0

    total_cupons_resgatados = CupomResgatado.query.count()
    taxa_resgate = (total_cupons_resgatados / len(todas_sessoes) * 100) if todas_sessoes else 0

    # Demanda
    contagem_por_hora = [0] * 24
    for s in todas_sessoes:
        contagem_por_hora[_hora_sao_paulo(s.criado_em)] += 1
    maximo_por_hora = max(contagem_por_hora) if any(contagem_por_hora) else 1
    hora_mais_movimentada = contagem_por_hora.index(max(contagem_por_hora)) if any(contagem_por_hora) else None

    contagem_por_nivel = {"baixa": 0, "media": 0, "alta": 0}
    for s in todas_sessoes:
        if s.nivel_lotacao in contagem_por_nivel:
            contagem_por_nivel[s.nivel_lotacao] += 1

    sessoes_em_pico = sum(1 for s in todas_sessoes if s.horario_pico)
    percentual_em_pico = (sessoes_em_pico / len(todas_sessoes) * 100) if todas_sessoes else 0

    # Tarifação
    receita_extra_pico = sum(
        (s.taxa_pico_kwh or 0) * (s.energia_kwh or 0) for s in sessoes_pagas if s.horario_pico
    )
    total_desconto_concedido = sum((s.desconto or 0) for s in sessoes_pagas)
    sessoes_pagas_com_desconto = sum(1 for s in sessoes_pagas if (s.desconto or 0) > 0)

    receita_base_pico_sem_taxa = sum(
        (s.tarifa_base_kwh or 0) * (s.energia_kwh or 0) for s in sessoes_pagas if s.horario_pico
    )

    # Faturamento
    receita_por_forma = {}
    for s in sessoes_pagas:
        forma = FORMA_PAGAMENTO_LABEL.get(s.forma_pagamento, "Outro")
        receita_por_forma[forma] = receita_por_forma.get(forma, 0) + s.valor_final
    receita_por_forma_lista = sorted(receita_por_forma.items(), key=lambda item: item[1], reverse=True)

    usuarios_vip_ativos = Usuario.query.filter_by(tipo="cliente", vip=True).count()
    receita_vip_mensal = usuarios_vip_ativos * PRECO_VIP_MENSAL

    receita_por_carregador = {}
    sessoes_por_carregador = {}
    for s in sessoes_pagas:
        tipo = s.carregador_tipo or "Desconhecido"
        receita_por_carregador[tipo] = receita_por_carregador.get(tipo, 0) + s.valor_final
        sessoes_por_carregador[tipo] = sessoes_por_carregador.get(tipo, 0) + 1
    receita_por_carregador_lista = sorted(
        receita_por_carregador.items(), key=lambda item: item[1], reverse=True
    )
    maior_receita_carregador = max(receita_por_carregador.values()) if receita_por_carregador else 0

    # Cupons
    cupons_todos = CupomResgatado.query.all()
    contagem_cupons_por_loja = {}
    for c in cupons_todos:
        contagem_cupons_por_loja[c.loja] = contagem_cupons_por_loja.get(c.loja, 0) + 1
    ranking_cupons = sorted(contagem_cupons_por_loja.items(), key=lambda item: item[1], reverse=True)
    maior_contagem_cupom = max(contagem_cupons_por_loja.values()) if contagem_cupons_por_loja else 0

    return {
        "total_sessoes": len(todas_sessoes),
        "receita_total": receita_total,
        "numero_sessoes_pagas": numero_sessoes_pagas,
        "ticket_medio": ticket_medio,
        "total_cupons_resgatados": total_cupons_resgatados,
        "taxa_resgate": round(taxa_resgate, 1),
        "contagem_por_hora": contagem_por_hora,
        "maximo_por_hora": maximo_por_hora,
        "hora_mais_movimentada": hora_mais_movimentada,
        "contagem_por_nivel": contagem_por_nivel,
        "sessoes_em_pico": sessoes_em_pico,
        "percentual_em_pico": round(percentual_em_pico, 1),
        "receita_extra_pico": receita_extra_pico,
        "total_desconto_concedido": total_desconto_concedido,
        "sessoes_pagas_com_desconto": sessoes_pagas_com_desconto,
        "receita_base_pico_sem_taxa": receita_base_pico_sem_taxa,
        "receita_por_forma_lista": receita_por_forma_lista,
        "usuarios_vip_ativos": usuarios_vip_ativos,
        "receita_vip_mensal": receita_vip_mensal,
        "receita_por_carregador_lista": receita_por_carregador_lista,
        "sessoes_por_carregador": sessoes_por_carregador,
        "maior_receita_carregador": maior_receita_carregador,
        "ranking_cupons": ranking_cupons,
        "maior_contagem_cupom": maior_contagem_cupom,
    }


@app.route("/gestor/painel")
@gestor_required
def painel_gestor():
    metricas = _calcular_metricas_outlet()

    return render_template(
        "painel_gestor.html",
        usuario=usuario_atual(),
        horas_de_pico=HORAS_DE_PICO,
        tarifas_por_kwh=TARIFAS_POR_KWH,
        desconto_percentual=DESCONTO_PERCENTUAL,
        taxa_pico_percentual=TAXA_PICO_PERCENTUAL,
        pagina_atual="painel_gestor",
        **metricas,
    )


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

TIPOS_CARREGADOR = [
    {"id": 1, "tipo": "AC 7kW · Posto 1", "potencia_kw": 7.0, "vip": False},
    {"id": 2, "tipo": "AC 7kW · Posto 2", "potencia_kw": 7.0, "vip": False},
    {"id": 3, "tipo": "AC 22kW · Posto 3", "potencia_kw": 22.0, "vip": False},
    {"id": 4, "tipo": "DC Rápido 60kW · Posto VIP", "potencia_kw": 60.0, "vip": True},
]

HORARIOS_PICO = [(8, 10), (18, 21)]

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

OFERTAS_OUTLET = [
    {"loja": "Cafeteria Central", "categoria": "Alimentação", "descricao": "15% de desconto em qualquer bebida quente"},
    {"loja": "Sabor Express", "categoria": "Alimentação", "descricao": "R$ 8 de cashback em compras acima de R$ 30"},
    {"loja": "Moda Urbana", "categoria": "Vestuário", "descricao": "20% de desconto em uma peça à sua escolha"},
    {"loja": "TechZone", "categoria": "Eletrônicos", "descricao": "10% de desconto em acessórios e fones de ouvido"},
    {"loja": "Livraria Page", "categoria": "Livraria", "descricao": "Compre 1 livro e leve um café de brinde"},
    {"loja": "Doce Ponto", "categoria": "Alimentação", "descricao": "R$ 5 de cashback em qualquer doce"},
    {"loja": "Beauty Spot", "categoria": "Beleza", "descricao": "25% de desconto em serviços expressos"},
    {"loja": "SportMax", "categoria": "Esportes", "descricao": "12% de desconto em calçados"},
    {"loja": "Praça de Alimentação", "categoria": "Alimentação", "descricao": "R$ 10 de cashback no almoço ou jantar"},
    {"loja": "Cinema Outlet", "categoria": "Lazer", "descricao": "30% de desconto em ingressos durante sua sessão"},
    {"loja": "Pet Shop Amigo", "categoria": "Pet", "descricao": "R$ 6 de cashback em produtos para pets"},
    {"loja": "Brinquedos & Cia", "categoria": "Infantil", "descricao": "15% de desconto em brinquedos selecionados"},
]


def _gerar_ofertas(tempo_total_min):
    quantidade = 3 if tempo_total_min >= 25 else 2
    escolhidas = random.sample(OFERTAS_OUTLET, k=min(quantidade, len(OFERTAS_OUTLET)))

    ofertas = []
    for oferta in escolhidas:
        ofertas.append({
            "loja": oferta["loja"],
            "categoria": oferta["categoria"],
            "descricao": oferta["descricao"],
            "codigo": f"CG-{random.randint(1000, 9999)}",
            "validade_min": int(round(tempo_total_min)),
        })
    return ofertas


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


def _status_aleatorio(pico):
    pesos = [0.15, 0.35, 0.50] if pico else [0.55, 0.30, 0.15]
    return random.choices(["disponivel", "carregando", "ocupado"], weights=pesos, k=1)[0]


@app.route("/api/carregamento/postos")
@login_required
def api_carregamento_postos():
    usuario = usuario_atual()
    pico = _esta_em_horario_de_pico()
    manutencao_idx = random.randrange(len(TIPOS_CARREGADOR)) if random.random() < 0.12 else None

    postos = []
    for i, info in enumerate(TIPOS_CARREGADOR):
        status = "manutencao" if i == manutencao_idx else _status_aleatorio(pico)
        bloqueado_vip = info["vip"] and not usuario.vip
        postos.append({
            "id": info["id"],
            "tipo": info["tipo"],
            "potencia_kw": info["potencia_kw"],
            "vip": info["vip"],
            "status": status,
            "bloqueado_vip": bloqueado_vip,
            "selecionavel": status == "disponivel" and not bloqueado_vip,
        })

    return jsonify({"postos": postos, "horario_pico": pico})


def _tempo_carga_com_curva(capacidade_kwh, percentual_inicial, percentual_final, potencia_kw):
    tempo_total_h = 0.0
    ponto = percentual_inicial

    if ponto < 80:
        fatia_final = min(percentual_final, 80)
        energia_fatia = capacidade_kwh * (fatia_final - ponto) / 100
        tempo_total_h += energia_fatia / potencia_kw
        ponto = fatia_final

    if ponto < percentual_final:
        potencia_reduzida = potencia_kw * 0.35
        energia_fatia = capacidade_kwh * (percentual_final - ponto) / 100
        tempo_total_h += energia_fatia / potencia_reduzida

    return tempo_total_h * 60


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
    carregador_id = _numero_ou_none(dados.get("carregador_id"), int)

    if percentual_atual is None or percentual_desejado is None:
        return jsonify({"erro": "Preencha o percentual atual e o percentual desejado."}), 400

    if not (0 <= percentual_atual <= 100) or not (0 <= percentual_desejado <= 100):
        return jsonify({"erro": "Percentuais devem estar entre 0 e 100."}), 400

    if percentual_desejado <= percentual_atual:
        return jsonify({"erro": "O percentual desejado deve ser maior que o atual."}), 400

    carregador = next((c for c in TIPOS_CARREGADOR if c["id"] == carregador_id), None)
    if carregador is None:
        return jsonify({"erro": "Selecione um posto de carregamento."}), 400

    if carregador["vip"] and not usuario.vip:
        return jsonify({"erro": "Este posto é exclusivo para assinantes VIP."}), 403

    capacidade_kwh = veiculo.capacidade_bateria_kwh
    pico = _esta_em_horario_de_pico()

    outros = [c for c in TIPOS_CARREGADOR if c["id"] != carregador_id]
    manutencao_idx = random.randrange(len(outros)) if random.random() < 0.12 else None

    status_postos = []
    ocupados = 1
    em_manutencao = 0

    for i, info in enumerate(outros):
        if i == manutencao_idx:
            status = "manutencao"
            em_manutencao += 1
        else:
            status = _status_aleatorio(pico)
            if status != "disponivel":
                ocupados += 1
        status_postos.append({"tipo": info["tipo"], "potencia_kw": info["potencia_kw"], "status": status})

    status_postos.append({"tipo": carregador["tipo"], "potencia_kw": carregador["potencia_kw"], "status": "carregando"})

    operacionais = len(TIPOS_CARREGADOR) - em_manutencao
    disponiveis = operacionais - ocupados
    taxa_ocupacao = ocupados / operacionais if operacionais else 1

    if taxa_ocupacao <= 0.25:
        nivel = "baixa"
    elif taxa_ocupacao <= 0.75:
        nivel = "media"
    else:
        nivel = "alta"

    energia_kwh = capacidade_kwh * (percentual_desejado - percentual_atual) / 100
    tempo_carregamento_min = _tempo_carga_com_curva(
        capacidade_kwh, percentual_atual, percentual_desejado, carregador["potencia_kw"]
    )
    tempo_espera_min = ESPERA_MINUTOS[nivel]
    tempo_total_min = tempo_carregamento_min + tempo_espera_min

    tarifa_base = TARIFAS_POR_KWH[nivel]
    taxa_pico_kwh = tarifa_base * TAXA_PICO_PERCENTUAL if pico else 0.0
    tarifa_final_kwh = tarifa_base + taxa_pico_kwh

    valor_total = energia_kwh * tarifa_final_kwh
    desconto = valor_total * DESCONTO_PERCENTUAL[nivel]
    valor_final = valor_total - desconto

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
        postos_operacionais=operacionais,
        postos_disponiveis=disponiveis,
        postos_ocupados=ocupados,
        horario_pico=pico,
        carregador_tipo=carregador["tipo"],
        carregador_potencia_kw=carregador["potencia_kw"],
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

        "postos_total": len(TIPOS_CARREGADOR),
        "postos_operacionais": operacionais,
        "postos_em_manutencao": em_manutencao,
        "postos_disponiveis": disponiveis,
        "postos_ocupados": ocupados,
        "status_postos": status_postos,

        "carregador_tipo": carregador["tipo"],
        "carregador_potencia_kw": carregador["potencia_kw"],

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
        "sugestao_horario": _gerar_sugestao_horario(ocupados, nivel),
        "ofertas": _gerar_ofertas(tempo_total_min),
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
        "redirect": url_for("historico"),
    })


# ---------------------------------------------------------------------------
# API - CUPONS RESGATADOS
# ---------------------------------------------------------------------------

@app.route("/api/cupons/resgatar", methods=["POST"])
@login_required
def api_cupom_resgatar():
    usuario = usuario_atual()
    dados = request.get_json(silent=True) or {}

    cupom = CupomResgatado(
        usuario_id=usuario.id,
        loja=(dados.get("loja") or "").strip()[:80],
        categoria=(dados.get("categoria") or "").strip()[:60],
        descricao=(dados.get("descricao") or "").strip()[:200],
        codigo=(dados.get("codigo") or "").strip()[:20],
    )
    db.session.add(cupom)
    db.session.commit()

    return jsonify({"mensagem": "Cupom resgatado! Confira em Minha Conta."}), 201


# ---------------------------------------------------------------------------
# API - ASSINATURA VIP
# ---------------------------------------------------------------------------

@app.route("/api/vip/assinar", methods=["POST"])
@login_required
def api_vip_assinar():
    usuario = usuario_atual()
    dados = request.get_json(silent=True) or {}
    forma_pagamento = dados.get("forma_pagamento")

    if forma_pagamento not in FORMAS_PAGAMENTO_VALIDAS:
        return jsonify({"erro": "Selecione uma forma de pagamento válida."}), 400

    usuario.vip = True
    db.session.commit()

    return jsonify({
        "mensagem": "Assinatura VIP ativada com sucesso!",
        "redirect": url_for("painel"),
    })


@app.route("/api/vip/cancelar", methods=["POST"])
@login_required
def api_vip_cancelar():
    usuario = usuario_atual()
    usuario.vip = False
    db.session.commit()
    return jsonify({"mensagem": "Assinatura VIP cancelada.", "redirect": url_for("painel")})


# ---------------------------------------------------------------------------
# API - CHATBOT DO CLIENTE (Google Gemini)
# ---------------------------------------------------------------------------

CHAT_MODELO = "gemini-3.5-flash-lite"


def _montar_system_prompt(usuario):
    catalogo_lojas = "\n".join(
        f"- {o['loja']} ({o['categoria']}): {o['descricao']}" for o in OFERTAS_OUTLET
    )

    tipos_texto = (
        "- AC 7kW (Wallbox padrão, 2 unidades): carregador mais comum e barato de instalar. "
        "Ideal para quem vai ficar tempo suficiente parado (compras, refeição). Carrega mais devagar.\n"
        "- AC 22kW (Wallbox trifásico, 1 unidade): opção intermediária, cerca de 3x mais rápida "
        "que o AC 7kW.\n"
        "- DC Rápido 60kW (1 unidade, EXCLUSIVO PARA ASSINANTES VIP): o mais rápido do outlet. "
        "Só pode ser usado por quem tem a assinatura VIP da plataforma."
    )

    contexto_pessoal = f" O usuário logado se chama {usuario.nome}."
    if usuario.vip:
        contexto_pessoal += " Ele já é assinante VIP, então tem acesso ao posto DC Rápido 60kW."
    else:
        contexto_pessoal += " Ele ainda não é assinante VIP."
    if usuario.veiculo:
        marca = usuario.veiculo.marca or ""
        modelo = usuario.veiculo.modelo or ""
        if marca or modelo:
            contexto_pessoal += f" O veículo cadastrado dele é um {marca} {modelo}.".strip()
        if usuario.veiculo.capacidade_bateria_kwh:
            contexto_pessoal += f" A bateria do carro tem {usuario.veiculo.capacidade_bateria_kwh} kWh de capacidade."

    return f"""Você é o assistente virtual do ChargeGrid Intelligence, uma plataforma que
simula uma rede de carregadores de veículos elétricos em um outlet comercial (projeto do
FIAP EV Challenge 2026, em parceria com a GoodWe).

SEU PAPEL, nesta ordem de prioridade:
1. Ajudar o usuário a decidir o MELHOR HORÁRIO para carregar. Horários de pico do outlet:
   08h-10h e 18h-21h (horário de São Paulo) — mais fila e tarifa mais cara nesses horários.
2. Recomendar LOJAS DO OUTLET quando o usuário quiser comprar algo ou perguntar sobre
   promoções. Use APENAS as lojas e ofertas reais listadas abaixo — nunca invente uma:
{catalogo_lojas}
3. Explicar a DIFERENÇA ENTRE OS CARREGADORES quando perguntado:
{tipos_texto}
   O usuário escolhe manualmente qual posto usar entre os disponíveis no momento. Também
   vale explicar que a carga é rápida até 80% da bateria e desacelera bastante depois disso.
4. Tirar dúvidas gerais sobre a plataforma e sobre carros elétricos/recarga em geral.

FORMATO DA RESPOSTA: estruture com tópicos usando "-" para listas e "**texto**" para
destacar nomes de lojas, carregadores ou valores importantes, sempre que isso deixar a
resposta mais organizada e fácil de escanear visualmente. Seja breve e direto — no máximo
4-5 frases de texto corrido, além dos tópicos quando houver.
{contexto_pessoal}"""


@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    if gemini_client is None:
        return jsonify({
            "erro": "O assistente ainda não foi configurado. Verifique se a GOOGLE_API_KEY está definida no .env.",
        }), 503

    dados = request.get_json(silent=True) or {}
    mensagens = dados.get("mensagens") or []

    if not mensagens:
        return jsonify({"erro": "Envie uma mensagem."}), 400

    usuario = usuario_atual()
    system_prompt = _montar_system_prompt(usuario)

    historico_convertido = []
    for msg in mensagens[-12:]:
        papel = "model" if msg.get("role") == "assistant" else "user"
        historico_convertido.append({"role": papel, "parts": [{"text": msg.get("content", "")}]})

    try:
        resposta = gemini_client.models.generate_content(
            model=CHAT_MODELO,
            contents=historico_convertido,
            config={
                "system_instruction": system_prompt,
                "max_output_tokens": 400,
            },
        )
        return jsonify({"resposta": resposta.text})
    except Exception as erro:
        app.logger.error(f"Erro ao chamar a API do Gemini: {erro}")
        return jsonify({"erro": "Não foi possível falar com o assistente agora. Tente novamente em instantes."}), 502


# ---------------------------------------------------------------------------
# API - ASSISTENTE DO GESTOR (Google Gemini + dados agregados reais)
# ---------------------------------------------------------------------------

def _montar_system_prompt_gestor(usuario, m):
    resumo_forma = "\n".join(
        f"- {forma}: R$ {valor:.2f}" for forma, valor in m["receita_por_forma_lista"]
    ) or "Nenhuma sessão paga ainda."

    resumo_carregador = "\n".join(
        f"- {tipo}: R$ {valor:.2f} ({m['sessoes_por_carregador'][tipo]} sessão(ões))"
        for tipo, valor in m["receita_por_carregador_lista"]
    ) or "Nenhuma sessão paga ainda."

    resumo_cupons = "\n".join(
        f"- {loja}: {contagem} resgate(s)" for loja, contagem in m["ranking_cupons"][:5]
    ) or "Nenhum cupom resgatado ainda."

    hora_pico_texto = f"{m['hora_mais_movimentada']}h" if m["hora_mais_movimentada"] is not None else "ainda não há dados suficientes"

    return f"""Você é o assistente de gestão do ChargeGrid Intelligence, uma plataforma de
carregadores de veículos elétricos instalada num outlet comercial (parceria FIAP EV
Challenge 2026 x GoodWe). Você fala com o GESTOR do outlet, não com clientes finais — seu
papel é ajudar a interpretar os dados reais da operação e sugerir ações de negócio.

DADOS REAIS DA OPERAÇÃO ATÉ AGORA:
- Sessões totais registradas: {m['total_sessoes']}
- Sessões pagas: {m['numero_sessoes_pagas']}
- Receita total de recargas: R$ {m['receita_total']:.2f}
- Ticket médio: R$ {m['ticket_medio']:.2f}
- Receita VIP mensal (assinantes ativos: {m['usuarios_vip_ativos']}): R$ {m['receita_vip_mensal']:.2f}
- Horário mais movimentado: {hora_pico_texto}
- % das sessões em horário de pico: {m['percentual_em_pico']}%
- Receita extra gerada pela taxa de pico: R$ {m['receita_extra_pico']:.2f}
- Desconto total concedido em baixa demanda: R$ {m['total_desconto_concedido']:.2f}
- Taxa de resgate de cupons: {m['taxa_resgate']}%

RECEITA POR FORMA DE PAGAMENTO:
{resumo_forma}

RECEITA POR TIPO DE CARREGADOR:
{resumo_carregador}

CUPONS MAIS RESGATADOS (top 5):
{resumo_cupons}

SEU PAPEL:
1. Responder perguntas do gestor sobre esses dados, com clareza e números concretos.
2. Sugerir ações de negócio baseadas nesses números (ex: ajustar a taxa de pico, negociar
   com uma loja específica, considerar mais um carregador de determinado tipo) — sempre
   citando o dado real que embasa a sugestão.
3. Nunca inventar números que não estejam listados acima. Se não houver dado suficiente
   para responder algo, diga isso claramente.
4. Ser direto e objetivo — no máximo 4-5 frases, usando "-" para listas e "**texto**"
   para destacar números e conclusões importantes.

O gestor logado se chama {usuario.nome}."""


@app.route("/api/chat-gestor", methods=["POST"])
@gestor_required
def api_chat_gestor():
    if gemini_client is None:
        return jsonify({
            "erro": "O assistente ainda não foi configurado. Verifique se a GOOGLE_API_KEY está definida no .env.",
        }), 503

    dados = request.get_json(silent=True) or {}
    mensagens = dados.get("mensagens") or []

    if not mensagens:
        return jsonify({"erro": "Envie uma mensagem."}), 400

    usuario = usuario_atual()
    metricas = _calcular_metricas_outlet()
    system_prompt = _montar_system_prompt_gestor(usuario, metricas)

    historico_convertido = []
    for msg in mensagens[-12:]:
        papel = "model" if msg.get("role") == "assistant" else "user"
        historico_convertido.append({"role": papel, "parts": [{"text": msg.get("content", "")}]})

    try:
        resposta = gemini_client.models.generate_content(
            model=CHAT_MODELO,
            contents=historico_convertido,
            config={
                "system_instruction": system_prompt,
                "max_output_tokens": 400,
            },
        )
        return jsonify({"resposta": resposta.text})
    except Exception as erro:
        app.logger.error(f"Erro ao chamar a API do Gemini (gestor): {erro}")
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
    destino = "painel_gestor" if usuario.tipo == "gestor" else "painel"
    return jsonify({"mensagem": "Login realizado com sucesso.", "redirect": url_for(destino)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("usuario_id", None)
    return jsonify({"mensagem": "Logout realizado."})


if __name__ == "__main__":
    app.run(debug=True)