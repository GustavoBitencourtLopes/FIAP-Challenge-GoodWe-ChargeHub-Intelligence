-- database/schema.sql
-- Estrutura base do banco de dados do ChargeGrid Intelligence.
-- Ainda não está conectada ao backend (que hoje usa SQLite local para
-- desenvolvimento rápido). Este arquivo é a referência para quando
-- migrarmos para PostgreSQL.

CREATE TABLE usuarios (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(120) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    senha_hash VARCHAR(255) NOT NULL,
    vip BOOLEAN DEFAULT FALSE,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE veiculos (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    placa VARCHAR(10),
    marca VARCHAR(60),
    modelo VARCHAR(60),
    ano INTEGER,
    capacidade_bateria_kwh NUMERIC(6,2),
    percentual_atual NUMERIC(5,2) DEFAULT 0,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessoes_carregamento (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    veiculo_id INTEGER NOT NULL REFERENCES veiculos(id) ON DELETE CASCADE,

    percentual_inicial NUMERIC(5,2),
    percentual_final NUMERIC(5,2),
    energia_kwh NUMERIC(6,2),

    tempo_carregamento_min NUMERIC(6,1),
    tempo_espera_min NUMERIC(6,1),
    tempo_total_min NUMERIC(6,1),

    nivel_lotacao VARCHAR(10),
    postos_operacionais INTEGER,
    postos_disponiveis INTEGER,
    postos_ocupados INTEGER,
    horario_pico BOOLEAN DEFAULT FALSE,

    carregador_tipo VARCHAR(40),
    carregador_potencia_kw NUMERIC(6,2),

    tarifa_base_kwh NUMERIC(6,2),
    taxa_pico_kwh NUMERIC(6,4),
    valor_total NUMERIC(8,2),
    desconto NUMERIC(8,2),
    valor_final NUMERIC(8,2),

    pago BOOLEAN DEFAULT FALSE,
    forma_pagamento VARCHAR(30),
    pago_em TIMESTAMP,

    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE cupons_resgatados (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    loja VARCHAR(80),
    categoria VARCHAR(60),
    descricao VARCHAR(200),
    codigo VARCHAR(20),
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);