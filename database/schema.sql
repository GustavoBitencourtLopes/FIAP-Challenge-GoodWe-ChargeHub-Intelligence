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
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE veiculos (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios (id) ON DELETE CASCADE,
    placa VARCHAR(10),
    marca VARCHAR(60),
    modelo VARCHAR(60),
    ano INTEGER,
    capacidade_bateria_kwh NUMERIC(6, 2), -- coletada no cadastro do veículo
    percentual_atual NUMERIC(5, 2) DEFAULT 0, -- atualizada a cada sessão de carregamento
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessoes_carregamento (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios (id) ON DELETE CASCADE,
    veiculo_id INTEGER NOT NULL REFERENCES veiculos (id) ON DELETE CASCADE,
    percentual_inicial NUMERIC(5, 2),
    percentual_final NUMERIC(5, 2),
    energia_kwh NUMERIC(6, 2),
    tempo_carregamento_min NUMERIC(6, 1),
    tempo_espera_min NUMERIC(6, 1),
    tempo_total_min NUMERIC(6, 1),
    nivel_lotacao VARCHAR(10), -- baixa | media | alta
    postos_operacionais INTEGER,
    postos_disponiveis INTEGER,
    postos_ocupados INTEGER,
    horario_pico BOOLEAN DEFAULT FALSE,
    tarifa_base_kwh NUMERIC(6, 2),
    taxa_pico_kwh NUMERIC(6, 4),
    valor_total NUMERIC(8, 2),
    desconto NUMERIC(8, 2),
    valor_final NUMERIC(8, 2),
    pago BOOLEAN DEFAULT FALSE,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);