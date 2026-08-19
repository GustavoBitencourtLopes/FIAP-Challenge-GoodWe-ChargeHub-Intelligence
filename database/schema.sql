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
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    placa VARCHAR(10),
    marca VARCHAR(60),
    modelo VARCHAR(60),
    ano INTEGER,
    -- Preenchidos futuramente pela funcionalidade "Fazer carregamento":
    capacidade_bateria_kwh NUMERIC(6,2),
    percentual_atual NUMERIC(5,2) DEFAULT 0,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);