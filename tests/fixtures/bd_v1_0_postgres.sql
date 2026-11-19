--
-- PostgreSQL database dump
--

\restrict jii8ffNFhaOTDvJyTFF0oRunTm91l3gmXhris0hMx6nbnotAdr2rQAwl6PuZaM0

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    id integer NOT NULL,
    dedup_key character varying(96) NOT NULL,
    kind character varying(16) NOT NULL,
    change_event_id integer,
    assignment_id integer,
    request_id integer,
    source_code character varying(64) NOT NULL,
    region character varying(32) NOT NULL,
    resource character varying(256) NOT NULL,
    priority integer,
    operator_name character varying(128),
    title character varying(256) NOT NULL,
    status character varying(16) NOT NULL,
    payload json NOT NULL,
    created_at timestamp without time zone NOT NULL,
    acknowledged_at timestamp without time zone,
    acknowledged_by character varying(64)
);


--
-- Name: alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alerts_id_seq OWNED BY public.alerts.id;


--
-- Name: api_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_tokens (
    id integer NOT NULL,
    user_id integer NOT NULL,
    token_hash character varying(64) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    revoked boolean NOT NULL
);


--
-- Name: api_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_tokens_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_tokens_id_seq OWNED BY public.api_tokens.id;


--
-- Name: assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assignments (
    id integer NOT NULL,
    request_id integer NOT NULL,
    slot_id integer NOT NULL,
    operator_id integer NOT NULL,
    change_event_id integer,
    quantity integer NOT NULL,
    status character varying(16) NOT NULL,
    live_request_id integer,
    created_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    closed_at timestamp without time zone,
    reason text NOT NULL
);


--
-- Name: assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.assignments_id_seq OWNED BY public.assignments.id;


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    ts timestamp without time zone NOT NULL,
    username character varying(64) NOT NULL,
    action character varying(64) NOT NULL,
    target character varying(128) NOT NULL,
    detail json NOT NULL,
    ip character varying(64) NOT NULL
);


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: change_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.change_events (
    id integer NOT NULL,
    event_key character varying(64) NOT NULL,
    source_id integer NOT NULL,
    slot_id integer,
    region character varying(32) NOT NULL,
    resource_key character varying(128) NOT NULL,
    resource_type character varying(64) NOT NULL,
    resource_name character varying(256) NOT NULL,
    change_type character varying(32) NOT NULL,
    before json NOT NULL,
    after json NOT NULL,
    relevant boolean NOT NULL,
    observed_at timestamp without time zone,
    detected_at timestamp without time zone NOT NULL,
    detection_latency_ms double precision,
    processed boolean NOT NULL
);


--
-- Name: change_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.change_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: change_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.change_events_id_seq OWNED BY public.change_events.id;


--
-- Name: channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.channels (
    id integer NOT NULL,
    name character varying(64) NOT NULL,
    type character varying(16) NOT NULL,
    config json NOT NULL,
    enabled boolean NOT NULL
);


--
-- Name: channels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.channels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: channels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.channels_id_seq OWNED BY public.channels.id;


--
-- Name: deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deliveries (
    id integer NOT NULL,
    alert_id integer NOT NULL,
    channel_id integer NOT NULL,
    status character varying(16) NOT NULL,
    attempts integer NOT NULL,
    next_attempt_at timestamp without time zone NOT NULL,
    last_error text,
    delivered_at timestamp without time zone,
    latency_ms double precision
);


--
-- Name: deliveries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.deliveries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: deliveries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.deliveries_id_seq OWNED BY public.deliveries.id;


--
-- Name: notification_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_log (
    id integer NOT NULL,
    ts timestamp without time zone NOT NULL,
    alert_id integer NOT NULL,
    channel character varying(64) NOT NULL,
    outcome character varying(16) NOT NULL,
    detail text NOT NULL
);


--
-- Name: notification_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notification_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notification_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notification_log_id_seq OWNED BY public.notification_log.id;


--
-- Name: operators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operators (
    id integer NOT NULL,
    name character varying(128) NOT NULL,
    user_id integer,
    regions json NOT NULL,
    active boolean NOT NULL,
    max_concurrent integer NOT NULL,
    current_load integer NOT NULL,
    last_assigned_at timestamp without time zone,
    CONSTRAINT ck_operator_load_nonneg CHECK ((current_load >= 0))
);


--
-- Name: operators_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.operators_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: operators_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.operators_id_seq OWNED BY public.operators.id;


--
-- Name: poll_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.poll_log (
    id integer NOT NULL,
    source_id integer NOT NULL,
    started_at timestamp without time zone NOT NULL,
    duration_ms double precision NOT NULL,
    outcome character varying(24) NOT NULL,
    attempts integer NOT NULL,
    items integer NOT NULL,
    changes integer NOT NULL,
    duplicates integer NOT NULL,
    unchanged integer NOT NULL,
    error text
);


--
-- Name: poll_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.poll_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: poll_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.poll_log_id_seq OWNED BY public.poll_log.id;


--
-- Name: regions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regions (
    code character varying(32) NOT NULL,
    name character varying(128) NOT NULL
);


--
-- Name: request_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_events (
    id integer NOT NULL,
    request_id integer NOT NULL,
    ts timestamp without time zone NOT NULL,
    from_status character varying(16),
    to_status character varying(16) NOT NULL,
    actor character varying(64) NOT NULL,
    note text NOT NULL
);


--
-- Name: request_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.request_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: request_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.request_events_id_seq OWNED BY public.request_events.id;


--
-- Name: requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.requests (
    id integer NOT NULL,
    code character varying(64) NOT NULL,
    holder character varying(128) NOT NULL,
    region character varying(32) NOT NULL,
    resource_type character varying(64) NOT NULL,
    priority integer NOT NULL,
    quantity integer NOT NULL,
    conditions json NOT NULL,
    status character varying(16) NOT NULL,
    operator_id integer,
    attempts integer NOT NULL,
    last_error text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.requests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.requests_id_seq OWNED BY public.requests.id;


--
-- Name: settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings (
    key character varying(64) NOT NULL,
    value json
);


--
-- Name: sim_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sim_items (
    id integer NOT NULL,
    feed character varying(64) NOT NULL,
    resource_key character varying(128) NOT NULL,
    name character varying(256) NOT NULL,
    region character varying(32) NOT NULL,
    resource_type character varying(64) NOT NULL,
    starts_at timestamp without time zone,
    capacity integer NOT NULL,
    available integer NOT NULL,
    cancelled boolean NOT NULL,
    changed_at timestamp without time zone NOT NULL,
    CONSTRAINT ck_sim_available_nonneg CHECK ((available >= 0))
);


--
-- Name: sim_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sim_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sim_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sim_items_id_seq OWNED BY public.sim_items.id;


--
-- Name: sim_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sim_runs (
    id integer NOT NULL,
    started_at timestamp without time zone NOT NULL,
    finished_at timestamp without time zone,
    params json NOT NULL,
    results json NOT NULL,
    status character varying(16) NOT NULL
);


--
-- Name: sim_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sim_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sim_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sim_runs_id_seq OWNED BY public.sim_runs.id;


--
-- Name: slot_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.slot_states (
    id integer NOT NULL,
    source_id integer NOT NULL,
    resource_key character varying(128) NOT NULL,
    region character varying(32) NOT NULL,
    resource_type character varying(64) NOT NULL,
    name character varying(256) NOT NULL,
    starts_at timestamp without time zone,
    capacity integer,
    available integer NOT NULL,
    held integer NOT NULL,
    status character varying(16) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    version integer NOT NULL,
    ever_available boolean NOT NULL,
    missing_count integer NOT NULL,
    first_seen_at timestamp without time zone NOT NULL,
    last_seen_at timestamp without time zone NOT NULL,
    attributes json NOT NULL,
    CONSTRAINT ck_slot_held_nonneg CHECK ((held >= 0))
);


--
-- Name: slot_states_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.slot_states_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: slot_states_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.slot_states_id_seq OWNED BY public.slot_states.id;


--
-- Name: sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sources (
    id integer NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    region character varying(32) NOT NULL,
    category character varying(64) NOT NULL,
    availability_type character varying(32) NOT NULL,
    connector_type character varying(32) NOT NULL,
    config json NOT NULL,
    status character varying(16) NOT NULL,
    interval_s double precision NOT NULL,
    jitter_pct double precision NOT NULL,
    rate_limit_per_min integer NOT NULL,
    max_retries integer NOT NULL,
    circuit_state character varying(16) NOT NULL,
    circuit_opened_at timestamp without time zone,
    circuit_open_count integer NOT NULL,
    consecutive_failures integer NOT NULL,
    next_poll_at timestamp without time zone,
    last_polled_at timestamp without time zone,
    last_success_at timestamp without time zone,
    last_result json NOT NULL,
    last_error text,
    total_polls integer NOT NULL,
    total_errors integer NOT NULL,
    total_changes integer NOT NULL,
    avg_latency_ms double precision NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: sources_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sources_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sources_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sources_id_seq OWNED BY public.sources.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(64) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(16) NOT NULL,
    active boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: alerts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts ALTER COLUMN id SET DEFAULT nextval('public.alerts_id_seq'::regclass);


--
-- Name: api_tokens id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_tokens ALTER COLUMN id SET DEFAULT nextval('public.api_tokens_id_seq'::regclass);


--
-- Name: assignments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments ALTER COLUMN id SET DEFAULT nextval('public.assignments_id_seq'::regclass);


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: change_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_events ALTER COLUMN id SET DEFAULT nextval('public.change_events_id_seq'::regclass);


--
-- Name: channels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels ALTER COLUMN id SET DEFAULT nextval('public.channels_id_seq'::regclass);


--
-- Name: deliveries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliveries ALTER COLUMN id SET DEFAULT nextval('public.deliveries_id_seq'::regclass);


--
-- Name: notification_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_log ALTER COLUMN id SET DEFAULT nextval('public.notification_log_id_seq'::regclass);


--
-- Name: operators id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators ALTER COLUMN id SET DEFAULT nextval('public.operators_id_seq'::regclass);


--
-- Name: poll_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.poll_log ALTER COLUMN id SET DEFAULT nextval('public.poll_log_id_seq'::regclass);


--
-- Name: request_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_events ALTER COLUMN id SET DEFAULT nextval('public.request_events_id_seq'::regclass);


--
-- Name: requests id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests ALTER COLUMN id SET DEFAULT nextval('public.requests_id_seq'::regclass);


--
-- Name: sim_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_items ALTER COLUMN id SET DEFAULT nextval('public.sim_items_id_seq'::regclass);


--
-- Name: sim_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_runs ALTER COLUMN id SET DEFAULT nextval('public.sim_runs_id_seq'::regclass);


--
-- Name: slot_states id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.slot_states ALTER COLUMN id SET DEFAULT nextval('public.slot_states_id_seq'::regclass);


--
-- Name: sources id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources ALTER COLUMN id SET DEFAULT nextval('public.sources_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Data for Name: alerts; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.alerts VALUES (1, 'asg:1', 'ASIGNACION', 16, 1, 11, 'sim-sur', 'SUR', 'Mesa Salón · sesión 2', 2, 'Operador 02', 'SOL-0011 asignada a Operador 02: Mesa Salón · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0011", "titular": "Expediente Colibr\u00ed 011", "cantidad": 1, "inicio": "2026-11-15T03:00:00", "vence_asignacion": "2026-11-12T15:12:52.982271Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:52.982271', NULL, NULL);
INSERT INTO public.alerts VALUES (2, 'asg:2', 'ASIGNACION', 34, 2, 24, 'sim-este', 'ESTE', 'Kit Delta · sesión 2', 2, 'Operador 03', 'SOL-0024 asignada a Operador 03: Kit Delta · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0024", "titular": "Expediente Nopal 024", "cantidad": 1, "inicio": "2026-11-17T03:00:00", "vence_asignacion": "2026-11-12T15:12:53.156737Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.156737', NULL, NULL);
INSERT INTO public.alerts VALUES (3, 'asg:3', 'ASIGNACION', 34, 3, 17, 'sim-este', 'ESTE', 'Kit Delta · sesión 2', 3, 'Operador 06 (todas)', 'SOL-0017 asignada a Operador 06 (todas): Kit Delta · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0017", "titular": "Expediente Cobre 017", "cantidad": 1, "inicio": "2026-11-17T03:00:00", "vence_asignacion": "2026-11-12T15:12:53.156737Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.156737', NULL, NULL);
INSERT INTO public.alerts VALUES (4, 'asg:4', 'ASIGNACION', 34, 4, 20, 'sim-este', 'ESTE', 'Kit Delta · sesión 2', 3, 'Operador 03', 'SOL-0020 asignada a Operador 03: Kit Delta · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0020", "titular": "Expediente Colibr\u00ed 020", "cantidad": 1, "inicio": "2026-11-17T03:00:00", "vence_asignacion": "2026-11-12T15:12:53.156737Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.156737', NULL, NULL);
INSERT INTO public.alerts VALUES (5, 'asg:5', 'ASIGNACION', 36, 5, 27, 'sim-oeste', 'OESTE', 'Turno Consulta A · sesión 1', 1, 'Operador 04', 'SOL-0027 asignada a Operador 04: Turno Consulta A · sesión 1', 'ENVIADA', '{"solicitud": "SOL-0027", "titular": "Expediente Cobre 027", "cantidad": 1, "inicio": "2026-11-14T00:00:00", "vence_asignacion": "2026-11-12T15:12:53.250162Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.250162', NULL, NULL);
INSERT INTO public.alerts VALUES (6, 'asg:6', 'ASIGNACION', 38, 6, 25, 'sim-oeste', 'OESTE', 'Turno Consulta A · sesión 3', 1, 'Operador 06 (todas)', 'SOL-0025 asignada a Operador 06 (todas): Turno Consulta A · sesión 3', 'ENVIADA', '{"solicitud": "SOL-0025", "titular": "Expediente Jade 025", "cantidad": 1, "inicio": "2026-11-14T06:00:00", "vence_asignacion": "2026-11-12T15:12:53.286353Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.286353', NULL, NULL);
INSERT INTO public.alerts VALUES (7, 'asg:7', 'ASIGNACION', 38, 7, 26, 'sim-oeste', 'OESTE', 'Turno Consulta A · sesión 3', 2, 'Operador 04', 'SOL-0026 asignada a Operador 04: Turno Consulta A · sesión 3', 'ENVIADA', '{"solicitud": "SOL-0026", "titular": "Expediente Colibr\u00ed 026", "cantidad": 1, "inicio": "2026-11-14T06:00:00", "vence_asignacion": "2026-11-12T15:12:53.286353Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.286353', NULL, NULL);
INSERT INTO public.alerts VALUES (8, 'asg:8', 'ASIGNACION', 60, 8, 29, 'sim-oeste', 'OESTE', 'Turno Taller · sesión 1', 4, 'Operador 04', 'SOL-0029 asignada a Operador 04: Turno Taller · sesión 1', 'ENVIADA', '{"solicitud": "SOL-0029", "titular": "Expediente Jade 029", "cantidad": 1, "inicio": "2026-11-16T00:00:00", "vence_asignacion": "2026-11-12T15:12:53.845591Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.845591', NULL, NULL);
INSERT INTO public.alerts VALUES (9, 'asg:9', 'ASIGNACION', 60, 9, 30, 'sim-oeste', 'OESTE', 'Turno Taller · sesión 1', 4, 'Operador 06 (todas)', 'SOL-0030 asignada a Operador 06 (todas): Turno Taller · sesión 1', 'ENVIADA', '{"solicitud": "SOL-0030", "titular": "Expediente \u00d3palo 030", "cantidad": 2, "inicio": "2026-11-16T00:00:00", "vence_asignacion": "2026-11-12T15:12:53.845591Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:53.845591', NULL, NULL);
INSERT INTO public.alerts VALUES (10, 'asg:10', 'ASIGNACION', 61, 10, 36, 'sim-centro', 'CENTRO', 'Feria Nébula · sesión 2', 1, 'Operador 05', 'SOL-0036 asignada a Operador 05: Feria Nébula · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0036", "titular": "Expediente Lince 036", "cantidad": 1, "inicio": "2026-11-14T03:00:00", "vence_asignacion": "2026-11-12T15:12:54.041360Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:54.04136', NULL, NULL);
INSERT INTO public.alerts VALUES (11, 'asg:11', 'ASIGNACION', 61, 11, 37, 'sim-centro', 'CENTRO', 'Feria Nébula · sesión 2', 1, 'Operador 05', 'SOL-0037 asignada a Operador 05: Feria Nébula · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0037", "titular": "Expediente Ceiba 037", "cantidad": 1, "inicio": "2026-11-14T03:00:00", "vence_asignacion": "2026-11-12T15:12:54.041360Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:54.04136', NULL, NULL);
INSERT INTO public.alerts VALUES (12, 'evt:98335ac096904ea0c5b909c597e0e6c51e2f84e57fb92844e1facab3f34c1311', 'DISPONIBILIDAD', 63, NULL, NULL, 'sim-oeste', 'OESTE', 'Turno Consulta A · sesión 2', NULL, NULL, 'Cancelacion: Turno Consulta A · sesión 2', 'ENVIADA', '{"cambio": "CANCELACION", "antes": {"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}, "despues": {"available": 0, "capacity": 8, "status": "CANCELADO", "starts_at": "2026-11-14T03:00:00"}}', '2026-11-12 14:57:54.265563', NULL, NULL);
INSERT INTO public.alerts VALUES (13, 'evt:e0d2b3620915baf8d02ff345bd42e5d578f2602f07cf4c38a394bec815e4a393', 'DISPONIBILIDAD', 66, NULL, NULL, 'sim-norte', 'NORTE', 'Concierto Aurora · sesión 2', NULL, NULL, 'Cambio Horario: Concierto Aurora · sesión 2', 'ENVIADA', '{"cambio": "CAMBIO_HORARIO", "antes": {"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}, "despues": {"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}}', '2026-11-12 14:57:54.447504', NULL, NULL);
INSERT INTO public.alerts VALUES (14, 'evt:b51d2425db5102037e92ba9d69f20a77bd8a45e91947d8e44382f78510a508d0', 'DISPONIBILIDAD', 67, NULL, NULL, 'sim-norte', 'NORTE', 'Gala Cometa · sesión 2', NULL, NULL, 'Cancelacion: Gala Cometa · sesión 2', 'ENVIADA', '{"cambio": "CANCELACION", "antes": {"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-16T03:00:00"}, "despues": {"available": 0, "capacity": 10, "status": "CANCELADO", "starts_at": "2026-11-16T03:00:00"}}', '2026-11-12 14:57:54.454893', NULL, NULL);
INSERT INTO public.alerts VALUES (15, 'asg:12', 'ASIGNACION', 68, 12, 16, 'sim-sur', 'SUR', 'Mesa Barra · sesión 1', 1, 'Operador 02', 'SOL-0016 asignada a Operador 02: Mesa Barra · sesión 1', 'ENVIADA', '{"solicitud": "SOL-0016", "titular": "Expediente Cobre 016", "cantidad": 2, "inicio": "2026-11-17T00:00:00", "vence_asignacion": "2026-11-12T15:12:54.463924Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:54.463924', NULL, NULL);
INSERT INTO public.alerts VALUES (16, 'asg:13', 'ASIGNACION', 69, 13, 15, 'sim-sur', 'SUR', 'Mesa Barra · sesión 2', 2, 'Operador 02', 'SOL-0015 asignada a Operador 02: Mesa Barra · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0015", "titular": "Expediente Ceiba 015", "cantidad": 1, "inicio": "2026-11-17T03:00:00", "vence_asignacion": "2026-11-12T15:12:54.486108Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:54.486108', NULL, NULL);
INSERT INTO public.alerts VALUES (17, 'asg:14', 'ASIGNACION', 70, 14, 22, 'sim-este', 'ESTE', 'Kit Alfa · sesión 2', 4, 'Operador 03', 'SOL-0022 asignada a Operador 03: Kit Alfa · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0022", "titular": "Expediente Quetzal 022", "cantidad": 1, "inicio": "2026-11-14T03:00:00", "vence_asignacion": "2026-11-12T15:12:54.512238Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:54.512238', NULL, NULL);
INSERT INTO public.alerts VALUES (18, 'evt:b7c509b5e40412a542ae8e3d8b12847f03e63b36670a5cd8dc3c0d1bdca113c8', 'DISPONIBILIDAD', 71, NULL, NULL, 'sim-este', 'ESTE', 'Kit Alfa · sesión 3', NULL, NULL, 'Nueva Disponibilidad: Kit Alfa · sesión 3 (sin solicitud compatible asignable)', 'ENVIADA', '{"cambio": "NUEVA_DISPONIBILIDAD", "antes": {"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}, "despues": {"available": 1, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}}', '2026-11-12 14:57:54.533198', NULL, NULL);
INSERT INTO public.alerts VALUES (19, 'evt:fb78485bf66a36ce25a694b64775c26c730f4ab0791c18b41d7f6765a511a77e', 'DISPONIBILIDAD', 72, NULL, NULL, 'sim-este', 'ESTE', 'Kit Beta · sesión 3', NULL, NULL, 'Nueva Disponibilidad: Kit Beta · sesión 3 (sin solicitud compatible asignable)', 'ENVIADA', '{"cambio": "NUEVA_DISPONIBILIDAD", "antes": {"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}, "despues": {"available": 1, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}}', '2026-11-12 14:57:54.548332', NULL, NULL);
INSERT INTO public.alerts VALUES (20, 'evt:bb75b507e16363966cc6a168f05d0d6685574dc1e4f5f9f678e74bc7fcd35ce2', 'DISPONIBILIDAD', 73, NULL, NULL, 'sim-este', 'ESTE', 'Kit Delta · sesión 2', NULL, NULL, 'Cambio Horario: Kit Delta · sesión 2', 'ENVIADA', '{"cambio": "CAMBIO_HORARIO", "antes": {"available": 3, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}, "despues": {"available": 3, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-17T01:00:00"}}', '2026-11-12 14:57:54.564484', NULL, NULL);
INSERT INTO public.alerts VALUES (21, 'asg:15', 'ASIGNACION', 74, 15, 27, 'sim-oeste', 'OESTE', 'Turno Consulta B · sesión 2', 1, 'Operador 04', 'SOL-0027 asignada a Operador 04: Turno Consulta B · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0027", "titular": "Expediente Cobre 027", "cantidad": 1, "inicio": "2026-11-15T03:00:00", "vence_asignacion": "2026-11-12T15:12:54.576877Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:54.576877', NULL, NULL);
INSERT INTO public.alerts VALUES (22, 'asg:16', 'ASIGNACION', 74, 16, 26, 'sim-oeste', 'OESTE', 'Turno Consulta B · sesión 2', 2, 'Operador 04', 'SOL-0026 asignada a Operador 04: Turno Consulta B · sesión 2', 'ENVIADA', '{"solicitud": "SOL-0026", "titular": "Expediente Colibr\u00ed 026", "cantidad": 1, "inicio": "2026-11-15T03:00:00", "vence_asignacion": "2026-11-12T15:12:54.576877Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:54.576877', NULL, NULL);
INSERT INTO public.alerts VALUES (23, 'evt:418a9e9b99302b80106a8321c3fe9dcf3ed9fa49925d238223ddb905e7f359ba', 'DISPONIBILIDAD', 75, NULL, NULL, 'sim-centro', 'CENTRO', 'Expo Prisma · sesión 3', NULL, NULL, 'Cambio Horario: Expo Prisma · sesión 3', 'ENVIADA', '{"cambio": "CAMBIO_HORARIO", "antes": {"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}, "despues": {"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T09:00:00"}}', '2026-11-12 14:57:54.627224', NULL, NULL);
INSERT INTO public.alerts VALUES (24, 'evt:b7268aaa300f08c9dcf769ef6ca07854d04912be5d44d34b6604dfccdf9eb3fc', 'DISPONIBILIDAD', 76, NULL, NULL, 'sim-centro', 'CENTRO', 'Foro Atlas · sesión 3', NULL, NULL, 'Cancelacion: Foro Atlas · sesión 3', 'ENVIADA', '{"cambio": "CANCELACION", "antes": {"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T06:00:00"}, "despues": {"available": 0, "capacity": 2, "status": "CANCELADO", "starts_at": "2026-11-16T06:00:00"}}', '2026-11-12 14:57:54.637928', NULL, NULL);
INSERT INTO public.alerts VALUES (25, 'evt:81020ddf20668e2c4afa5898493b6faa2cf403dac38c3d8b1be38a48fbf03a0a', 'DISPONIBILIDAD', 77, NULL, NULL, 'sim-sur', 'SUR', 'Mesa Terraza · sesión 3', NULL, NULL, 'Reaparicion: Mesa Terraza · sesión 3 (sin solicitud compatible asignable)', 'ENVIADA', '{"cambio": "REAPARICION", "antes": {"available": 0, "capacity": 4, "status": "CANCELADO", "starts_at": "2026-11-14T06:00:00"}, "despues": {"available": 1, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}}', '2026-11-12 14:57:55.095575', NULL, NULL);
INSERT INTO public.alerts VALUES (26, 'evt:48ad0f997f988bc2f83eb9d62e6375daf12232f908b3d933dca6abe0d96c77c1', 'DISPONIBILIDAD', 78, NULL, NULL, 'sim-sur', 'SUR', 'Mesa Salón · sesión 2', NULL, NULL, 'Aumento: Mesa Salón · sesión 2 (sin solicitud compatible asignable)', 'ENVIADA', '{"cambio": "AUMENTO", "antes": {"available": 1, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}, "despues": {"available": 4, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}}', '2026-11-12 14:57:55.10931', NULL, NULL);
INSERT INTO public.alerts VALUES (27, 'evt:1aac16873019a5ff6d77a121305c81501b100ef828c2358a8f92f9ed4ed0a903', 'DISPONIBILIDAD', 79, NULL, NULL, 'sim-sur', 'SUR', 'Mesa Barra · sesión 2', NULL, NULL, 'Cancelacion: Mesa Barra · sesión 2', 'ENVIADA', '{"cambio": "CANCELACION", "antes": {"available": 2, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}, "despues": {"available": 0, "capacity": 8, "status": "CANCELADO", "starts_at": "2026-11-17T03:00:00", "conflicto_retenidas": 1}}', '2026-11-12 14:57:55.123761', NULL, NULL);
INSERT INTO public.alerts VALUES (28, 'asg:17', 'ASIGNACION', NULL, 17, 15, 'sim-sur', 'SUR', 'Mesa Terraza · sesión 3', 2, 'Operador 02', 'SOL-0015 asignada a Operador 02: Mesa Terraza · sesión 3', 'ENVIADA', '{"solicitud": "SOL-0015", "titular": "Expediente Ceiba 015", "cantidad": 1, "inicio": "2026-11-14T06:00:00", "vence_asignacion": "2026-11-12T15:12:55.147786Z", "estado_solicitud": "ASIGNADO"}', '2026-11-12 14:57:55.147786', NULL, NULL);


--
-- Data for Name: api_tokens; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: assignments; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.assignments VALUES (1, 11, 17, 2, 16, 1, 'ACTIVA', 11, '2026-11-12 14:57:52.982271', '2026-11-12 15:12:52.982271', NULL, '');
INSERT INTO public.assignments VALUES (2, 24, 35, 3, 34, 1, 'ACTIVA', 24, '2026-11-12 14:57:53.156737', '2026-11-12 15:12:53.156737', NULL, '');
INSERT INTO public.assignments VALUES (3, 17, 35, 6, 34, 1, 'ACTIVA', 17, '2026-11-12 14:57:53.156737', '2026-11-12 15:12:53.156737', NULL, '');
INSERT INTO public.assignments VALUES (4, 20, 35, 3, 34, 1, 'ACTIVA', 20, '2026-11-12 14:57:53.156737', '2026-11-12 15:12:53.156737', NULL, '');
INSERT INTO public.assignments VALUES (6, 25, 39, 6, 38, 1, 'ACTIVA', 25, '2026-11-12 14:57:53.286353', '2026-11-12 15:12:53.286353', NULL, '');
INSERT INTO public.assignments VALUES (8, 29, 43, 4, 60, 1, 'ACTIVA', 29, '2026-11-12 14:57:53.845591', '2026-11-12 15:12:53.845591', NULL, '');
INSERT INTO public.assignments VALUES (9, 30, 43, 6, 60, 2, 'ACTIVA', 30, '2026-11-12 14:57:53.845591', '2026-11-12 15:12:53.845591', NULL, '');
INSERT INTO public.assignments VALUES (5, 27, 37, 4, 36, 1, 'CANCELADA', NULL, '2026-11-12 14:57:53.250162', '2026-11-12 15:12:53.250162', '2026-11-12 14:57:54.245967', 'el recurso dejó de estar disponible en la fuente');
INSERT INTO public.assignments VALUES (7, 26, 39, 4, 38, 1, 'CANCELADA', NULL, '2026-11-12 14:57:53.286353', '2026-11-12 15:12:53.286353', '2026-11-12 14:57:54.275059', 'el recurso dejó de estar disponible en la fuente');
INSERT INTO public.assignments VALUES (11, 37, 50, 5, 61, 1, 'CANCELADA', NULL, '2026-11-12 14:57:54.04136', '2026-11-12 15:12:54.04136', '2026-11-12 14:57:54.290461', 'el recurso dejó de estar disponible en la fuente');
INSERT INTO public.assignments VALUES (10, 36, 50, 5, 61, 1, 'CANCELADA', NULL, '2026-11-12 14:57:54.04136', '2026-11-12 15:12:54.04136', '2026-11-12 14:57:54.290461', 'el recurso dejó de estar disponible en la fuente');
INSERT INTO public.assignments VALUES (12, 16, 22, 2, 68, 2, 'ACTIVA', 16, '2026-11-12 14:57:54.463924', '2026-11-12 15:12:54.463924', NULL, '');
INSERT INTO public.assignments VALUES (14, 22, 26, 3, 70, 1, 'ACTIVA', 22, '2026-11-12 14:57:54.512238', '2026-11-12 15:12:54.512238', NULL, '');
INSERT INTO public.assignments VALUES (15, 27, 41, 4, 74, 1, 'ACTIVA', 27, '2026-11-12 14:57:54.576877', '2026-11-12 15:12:54.576877', NULL, '');
INSERT INTO public.assignments VALUES (16, 26, 41, 4, 74, 1, 'ACTIVA', 26, '2026-11-12 14:57:54.576877', '2026-11-12 15:12:54.576877', NULL, '');
INSERT INTO public.assignments VALUES (13, 15, 23, 2, 69, 1, 'CANCELADA', NULL, '2026-11-12 14:57:54.486108', '2026-11-12 15:12:54.486108', '2026-11-12 14:57:55.123761', 'el recurso dejó de estar disponible en la fuente');
INSERT INTO public.assignments VALUES (17, 15, 15, 2, NULL, 1, 'ACTIVA', 15, '2026-11-12 14:57:55.147786', '2026-11-12 15:12:55.147786', NULL, '');


--
-- Data for Name: audit_log; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: change_events; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.change_events VALUES (2, 'dc1cfbfe42c2b63cb11d28607be104b4e5174fe971ebbb12631ecc22e3474888', 1, 2, 'NORTE', 'NO-01', 'entrada', 'Concierto Aurora · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', false, '2026-11-12 14:57:52.330367', '2026-11-12 14:57:52.631692', 301.325, true);
INSERT INTO public.change_events VALUES (3, '5dbcd9d7e87fb07ea189a092500b7af385c40ebe81b782777e134c333546a367', 1, 3, 'NORTE', 'NO-02', 'entrada', 'Concierto Aurora · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', false, '2026-11-12 14:57:52.332775', '2026-11-12 14:57:52.631692', 298.917, true);
INSERT INTO public.change_events VALUES (4, 'b930ff3f54755e4fe666c3d79472d2c8ba4a4124518a59518d5ff8b8b7f07db8', 1, 4, 'NORTE', 'NO-10', 'entrada', 'Festival Brisa · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-15T00:00:00"}', false, '2026-11-12 14:57:52.335222', '2026-11-12 14:57:52.631692', 296.47, true);
INSERT INTO public.change_events VALUES (5, '79bbf3424a1edf8f1bb01a1e16b19263395c7dc2d8b66f2680891c09e00e496c', 1, 5, 'NORTE', 'NO-11', 'entrada', 'Festival Brisa · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', false, '2026-11-12 14:57:52.337833', '2026-11-12 14:57:52.631692', 293.859, true);
INSERT INTO public.change_events VALUES (6, '75354d5888a5a35e8232b80d3d7b531e3d28ab16cf46b7016beffdbc08c6f15a', 1, 6, 'NORTE', 'NO-12', 'entrada', 'Festival Brisa · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', false, '2026-11-12 14:57:52.33974', '2026-11-12 14:57:52.631692', 291.952, true);
INSERT INTO public.change_events VALUES (7, 'c5ed314d773a2623a31d10e6f13de83998fe415644bfbb6a28c24d5a977ba7a1', 1, 7, 'NORTE', 'NO-20', 'entrada', 'Gala Cometa · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T00:00:00"}', false, '2026-11-12 14:57:52.342465', '2026-11-12 14:57:52.631692', 289.22700000000003, true);
INSERT INTO public.change_events VALUES (8, '59349b5f52c17f0f420d917063aa51fd78b735631e87cf4eef42d7cd7b92c491', 1, 8, 'NORTE', 'NO-21', 'entrada', 'Gala Cometa · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-16T03:00:00"}', false, '2026-11-12 14:57:52.34438', '2026-11-12 14:57:52.631692', 287.312, true);
INSERT INTO public.change_events VALUES (9, '8d32df4f0251f77d61eac4466b01b621266e93e1923443b7ee71c1bf45c2755c', 1, 9, 'NORTE', 'NO-22', 'entrada', 'Gala Cometa · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T06:00:00"}', false, '2026-11-12 14:57:52.34716', '2026-11-12 14:57:52.631692', 284.532, true);
INSERT INTO public.change_events VALUES (10, '4b977939ba13bbd26ccc4195cc311ee66ebde532985b3dc8dbce6d52113a56f9', 1, 10, 'NORTE', 'NO-30', 'entrada', 'Teatro Lumen · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-17T00:00:00"}', false, '2026-11-12 14:57:52.349058', '2026-11-12 14:57:52.631692', 282.634, true);
INSERT INTO public.change_events VALUES (11, '965fa54ac682df2ba9f7fbc383d6af34c07d27a2f8c53022135282dd2aa49698', 1, 11, 'NORTE', 'NO-31', 'entrada', 'Teatro Lumen · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', false, '2026-11-12 14:57:52.350932', '2026-11-12 14:57:52.631692', 280.76, true);
INSERT INTO public.change_events VALUES (12, '9c314c4b5f4791a1b62ad940a89f6fa88fafdb80e2f12eaa37a7bcdff08adc31', 1, 12, 'NORTE', 'NO-32', 'entrada', 'Teatro Lumen · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-17T06:00:00"}', false, '2026-11-12 14:57:52.352881', '2026-11-12 14:57:52.631692', 278.811, true);
INSERT INTO public.change_events VALUES (13, 'b86d3c1057856e7fbecef7736c47153c9f4a00088c21d8ec653b35f82ac9ad66', 2, 13, 'SUR', 'SU-00', 'mesa', 'Mesa Terraza · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T00:00:00"}', false, '2026-11-12 14:57:52.620643', '2026-11-12 14:57:52.684903', 64.25999999999999, true);
INSERT INTO public.change_events VALUES (14, '801c2fb8404b80e443b4600d1b6c2dde892d886fa935e5bfd83755dd1288ad0c', 2, 14, 'SUR', 'SU-01', 'mesa', 'Mesa Terraza · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', false, '2026-11-12 14:57:52.356628', '2026-11-12 14:57:52.684903', 328.275, true);
INSERT INTO public.change_events VALUES (15, 'f6bb4bcf656561e2f5bbc1ea36cad84a04cec454c65745c473d6f8a6f2afcff9', 2, 16, 'SUR', 'SU-10', 'mesa', 'Mesa Salón · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-15T00:00:00"}', false, '2026-11-12 14:57:52.360763', '2026-11-12 14:57:52.684903', 324.14, true);
INSERT INTO public.change_events VALUES (16, 'f3d68a4168de390bc273ea01ed00b6d465ab0f34ac8cfdc63c3492990f027ae4', 2, 17, 'SUR', 'SU-11', 'mesa', 'Mesa Salón · sesión 2', 'NUEVA_DISPONIBILIDAD', '{}', '{"available": 1, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', true, '2026-11-12 14:57:52.612644', '2026-11-12 14:57:52.684903', 72.259, true);
INSERT INTO public.change_events VALUES (17, '0ae53b189961892935a43baf765172a90fce278bc2f295c413b528f3ebca5482', 2, 18, 'SUR', 'SU-12', 'mesa', 'Mesa Salón · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', false, '2026-11-12 14:57:52.365328', '2026-11-12 14:57:52.684903', 319.575, true);
INSERT INTO public.change_events VALUES (18, '2884301a4bf67cdf8bd650df6e63e538a4314e85195fa7b360c23569ce0754f5', 2, 19, 'SUR', 'SU-20', 'mesa', 'Mesa Jardín · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T00:00:00"}', false, '2026-11-12 14:57:52.367245', '2026-11-12 14:57:52.684903', 317.658, true);
INSERT INTO public.change_events VALUES (19, 'f3e3aced3dee3c7ac7d90c036b34686758b7e2f20583c6c5f9e382d15bf9055a', 2, 20, 'SUR', 'SU-21', 'mesa', 'Mesa Jardín · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-16T03:00:00"}', false, '2026-11-12 14:57:52.369561', '2026-11-12 14:57:52.684903', 315.342, true);
INSERT INTO public.change_events VALUES (20, '3860707c1874e98c6e5168faf918b23a9edea410060457093a9c7a696d5802c5', 2, 21, 'SUR', 'SU-22', 'mesa', 'Mesa Jardín · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-16T06:00:00"}', false, '2026-11-12 14:57:52.37132', '2026-11-12 14:57:52.684903', 313.583, true);
INSERT INTO public.change_events VALUES (21, 'e31dbcfe7eac170b82df9dba00d8679171c1cb136f9b46f52bdff3639dc19eef', 2, 22, 'SUR', 'SU-30', 'mesa', 'Mesa Barra · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-17T00:00:00"}', false, '2026-11-12 14:57:52.373233', '2026-11-12 14:57:52.684903', 311.67, true);
INSERT INTO public.change_events VALUES (22, 'dcaeb56fcc7e1a2745eadef0d3ad8c52b38d3d799fcf0fcaee7c2b9e77973b17', 2, 23, 'SUR', 'SU-31', 'mesa', 'Mesa Barra · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', false, '2026-11-12 14:57:52.374933', '2026-11-12 14:57:52.684903', 309.97, true);
INSERT INTO public.change_events VALUES (23, 'ec383ee834b58dc19815483e906eb2612cf7b303c5f671284af23b2f4f518651', 2, 24, 'SUR', 'SU-32', 'mesa', 'Mesa Barra · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-17T06:00:00"}', false, '2026-11-12 14:57:52.376437', '2026-11-12 14:57:52.684903', 308.466, true);
INSERT INTO public.change_events VALUES (24, 'acf84350ee49421a425bbd6a4c710408e22891af15072678aa4a57f5c54699d1', 3, 25, 'ESTE', 'ES-00', 'unidad', 'Kit Alfa · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-14T00:00:00"}', false, '2026-11-12 14:57:52.378108', '2026-11-12 14:57:52.728751', 350.643, true);
INSERT INTO public.change_events VALUES (25, 'd593d85996cd057eabafb9ace6e181a8a149c364c7034b402fecc033e8153cd1', 3, 26, 'ESTE', 'ES-01', 'unidad', 'Kit Alfa · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', false, '2026-11-12 14:57:52.379599', '2026-11-12 14:57:52.728751', 349.15200000000004, true);
INSERT INTO public.change_events VALUES (26, 'cb74cebeee5d0b5619ec65aa323e872d3f5e07d50bf66fa6bd7d4cd01bc84ffa', 3, 27, 'ESTE', 'ES-02', 'unidad', 'Kit Alfa · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', false, '2026-11-12 14:57:52.381215', '2026-11-12 14:57:52.728751', 347.536, true);
INSERT INTO public.change_events VALUES (27, '26814de54796dcc35b453ebbeaf0baa755a266d9e6610aac0acb56d03964966b', 3, 28, 'ESTE', 'ES-10', 'unidad', 'Kit Beta · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-15T00:00:00"}', false, '2026-11-12 14:57:52.38287', '2026-11-12 14:57:52.728751', 345.881, true);
INSERT INTO public.change_events VALUES (29, '3513f3b5ddcfe73702382c03f651bc214b0c40ff67c277c33c128aa503e30a97', 3, 30, 'ESTE', 'ES-12', 'unidad', 'Kit Beta · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', false, '2026-11-12 14:57:52.386441', '2026-11-12 14:57:52.728751', 342.31, true);
INSERT INTO public.change_events VALUES (30, 'e77329d234d6c5bb139e570fad5fedb9ed849f22155c86856ed934c3d74d8c45', 3, 31, 'ESTE', 'ES-20', 'unidad', 'Kit Gamma · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-16T00:00:00"}', false, '2026-11-12 14:57:52.609846', '2026-11-12 14:57:52.728751', 118.905, true);
INSERT INTO public.change_events VALUES (31, '4a9d94962b498593b5d2253e88849fe4c957cc9db4b210ce5f6cc6c5a465b8f8', 3, 32, 'ESTE', 'ES-21', 'unidad', 'Kit Gamma · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-16T03:00:00"}', false, '2026-11-12 14:57:52.390049', '2026-11-12 14:57:52.728751', 338.702, true);
INSERT INTO public.change_events VALUES (32, '1ced2478582746044a31f0d1dd6c55e725b904a5300cc781fdcf4ecef15e6b3c', 3, 33, 'ESTE', 'ES-22', 'unidad', 'Kit Gamma · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-16T06:00:00"}', false, '2026-11-12 14:57:52.391722', '2026-11-12 14:57:52.728751', 337.029, true);
INSERT INTO public.change_events VALUES (33, 'f163c03beffc7e9555a6a8aa7de405a4eb3de59de00dff4d18c31fa2a175cfee', 3, 34, 'ESTE', 'ES-30', 'unidad', 'Kit Delta · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-17T00:00:00"}', false, '2026-11-12 14:57:52.393638', '2026-11-12 14:57:52.728751', 335.113, true);
INSERT INTO public.change_events VALUES (34, '36e54c112ba15a9e548aa43d3cad9048dc461c5baee2e937443ede087f821f63', 3, 35, 'ESTE', 'ES-31', 'unidad', 'Kit Delta · sesión 2', 'NUEVA_DISPONIBILIDAD', '{}', '{"available": 3, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', true, '2026-11-12 14:57:52.599841', '2026-11-12 14:57:52.728751', 128.91, true);
INSERT INTO public.change_events VALUES (35, '00fa77d8d1dd37d1c4cf66469a80d276b42825ea001246f95816c05b010ab0d1', 3, 36, 'ESTE', 'ES-32', 'unidad', 'Kit Delta · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-17T06:00:00"}', false, '2026-11-12 14:57:52.397378', '2026-11-12 14:57:52.728751', 331.373, true);
INSERT INTO public.change_events VALUES (36, '5449ba128615b731522f4629bcadbbb3a4a4dae4a5fe347bcf2652ff695fdb52', 4, 37, 'OESTE', 'OE-00', 'turno', 'Turno Consulta A · sesión 1', 'NUEVA_DISPONIBILIDAD', '{}', '{"available": 1, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T00:00:00"}', true, '2026-11-12 14:57:52.61508', '2026-11-12 14:57:52.773128', 158.048, true);
INSERT INTO public.change_events VALUES (37, 'fd396aab6ca3f9020d33d9f6c231240a7d38af5c19bea8dcc0c86c2532ebc992', 4, 38, 'OESTE', 'OE-01', 'turno', 'Turno Consulta A · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', false, '2026-11-12 14:57:52.401122', '2026-11-12 14:57:52.773128', 372.00600000000003, true);
INSERT INTO public.change_events VALUES (38, '405f2cf3f2d3b8638b3e314b8e0dc185ba4a10267c1ab2e9f7771ba38757e5b7', 4, 39, 'OESTE', 'OE-02', 'turno', 'Turno Consulta A · sesión 3', 'NUEVA_DISPONIBILIDAD', '{}', '{"available": 2, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', true, '2026-11-12 14:57:52.624954', '2026-11-12 14:57:52.773128', 148.174, true);
INSERT INTO public.change_events VALUES (39, '9df2ed0060242fb4f1c346bb3f8d8aa7899e6f17276a836be807960945f7dff8', 4, 40, 'OESTE', 'OE-10', 'turno', 'Turno Consulta B · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-15T00:00:00"}', false, '2026-11-12 14:57:52.404489', '2026-11-12 14:57:52.773128', 368.639, true);
INSERT INTO public.change_events VALUES (40, '796539da1795aba0b60da1c32f7e95a51ffc11350de79604d118d2491c5ec592', 4, 41, 'OESTE', 'OE-11', 'turno', 'Turno Consulta B · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', false, '2026-11-12 14:57:52.406273', '2026-11-12 14:57:52.773128', 366.85499999999996, true);
INSERT INTO public.change_events VALUES (41, '22858a7082719ab7d2117e35d368cd8e542e6bdbe81cca19bfa7ce5e29218df2', 4, 42, 'OESTE', 'OE-12', 'turno', 'Turno Consulta B · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', false, '2026-11-12 14:57:52.407889', '2026-11-12 14:57:52.773128', 365.239, true);
INSERT INTO public.change_events VALUES (42, 'b4608137b4a3fab447b6606a4d87ac852b11225a1fef11ba3ee48e1e58ae0cc1', 4, 43, 'OESTE', 'OE-20', 'turno', 'Turno Taller · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-16T00:00:00"}', false, '2026-11-12 14:57:52.410056', '2026-11-12 14:57:52.773128', 363.072, true);
INSERT INTO public.change_events VALUES (43, '7d50419cab910516895ebd6ad75f09c1859a74517af5d3785ed5b01404731c4b', 4, 44, 'OESTE', 'OE-21', 'turno', 'Turno Taller · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T03:00:00"}', false, '2026-11-12 14:57:52.41245', '2026-11-12 14:57:52.773128', 360.678, true);
INSERT INTO public.change_events VALUES (44, 'fc8977f238c5f5beafeef0089e2d4e4061fea44f9a3ba3f48788d0994cf4636e', 4, 45, 'OESTE', 'OE-22', 'turno', 'Turno Taller · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-16T06:00:00"}', false, '2026-11-12 14:57:52.414302', '2026-11-12 14:57:52.773128', 358.82599999999996, true);
INSERT INTO public.change_events VALUES (45, 'db74c332e42224e3e1c2b0a1c7cda4e3380b0b1bfcd80f1b4f84a9986a13c809', 4, 46, 'OESTE', 'OE-30', 'turno', 'Turno Asesoría · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-17T00:00:00"}', false, '2026-11-12 14:57:52.415968', '2026-11-12 14:57:52.773128', 357.15999999999997, true);
INSERT INTO public.change_events VALUES (46, '050e25707fda7ab6fd0d08ef097f015b626d03d589835e653838767b5f8a7451', 4, 47, 'OESTE', 'OE-31', 'turno', 'Turno Asesoría · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', false, '2026-11-12 14:57:52.418024', '2026-11-12 14:57:52.773128', 355.104, true);
INSERT INTO public.change_events VALUES (47, 'be7bb54888de679ed44eac5ad36aca450ede29a6942cb75df36d637e41c6ca5f', 4, 48, 'OESTE', 'OE-32', 'turno', 'Turno Asesoría · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-17T06:00:00"}', false, '2026-11-12 14:57:52.622952', '2026-11-12 14:57:52.773128', 150.17600000000002, true);
INSERT INTO public.change_events VALUES (48, '4143cb0c87144679cf532b9833fd025a38be57550d489bf415e53a9cd1ae9a15', 5, 49, 'CENTRO', 'CE-00', 'entrada', 'Feria Nébula · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T00:00:00"}', false, '2026-11-12 14:57:52.421602', '2026-11-12 14:57:52.812951', 391.349, true);
INSERT INTO public.change_events VALUES (49, '137243d61bb9cfbd18cf19db806bedae85b02a79013b98174f220de38ea465df', 5, 50, 'CENTRO', 'CE-01', 'entrada', 'Feria Nébula · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', false, '2026-11-12 14:57:52.423439', '2026-11-12 14:57:52.812951', 389.512, true);
INSERT INTO public.change_events VALUES (50, '9d048897b701e99110534f0a3513ec6463f68066bcb34d50b894e5dd8260f6cd', 5, 51, 'CENTRO', 'CE-02', 'entrada', 'Feria Nébula · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', false, '2026-11-12 14:57:52.425396', '2026-11-12 14:57:52.812951', 387.555, true);
INSERT INTO public.change_events VALUES (51, 'dffcd04ad90c3eda363453222af73920d27a0c1528b92cda80b440cb4a764c2d', 5, 52, 'CENTRO', 'CE-10', 'entrada', 'Expo Prisma · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T00:00:00"}', false, '2026-11-12 14:57:52.427894', '2026-11-12 14:57:52.812951', 385.05699999999996, true);
INSERT INTO public.change_events VALUES (52, '85e91a376abb8acbde5bf98a80a7381cfb6fef7d441da0f2682ee3ad3d11c93d', 5, 53, 'CENTRO', 'CE-11', 'entrada', 'Expo Prisma · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', false, '2026-11-12 14:57:52.43036', '2026-11-12 14:57:52.812951', 382.591, true);
INSERT INTO public.change_events VALUES (53, 'a1726d05feecad7a5a6be6904acb3ce5048ef1228e24d30e4bb9180237dd8b17', 5, 54, 'CENTRO', 'CE-12', 'entrada', 'Expo Prisma · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', false, '2026-11-12 14:57:52.432076', '2026-11-12 14:57:52.812951', 380.875, true);
INSERT INTO public.change_events VALUES (54, 'dff60d14f10a1c847afae37549c3dbad132e6c855a4015150d14906a5176695c', 5, 55, 'CENTRO', 'CE-20', 'entrada', 'Foro Atlas · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-16T00:00:00"}', false, '2026-11-12 14:57:52.433971', '2026-11-12 14:57:52.812951', 378.97999999999996, true);
INSERT INTO public.change_events VALUES (1, '0711f2d209a164e664832c7c751892c47e2d9daf04e718269f2d2db086d24e65', 1, 1, 'NORTE', 'NO-00', 'entrada', 'Concierto Aurora · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T00:00:00"}', false, '2026-11-12 14:57:52.326087', '2026-11-12 14:57:52.631692', 305.605, true);
INSERT INTO public.change_events VALUES (28, 'c641c4e983f576ed92b1fb0f46cbd0bd8c99750c58eee86cc42218b4c1c2d153', 3, 29, 'ESTE', 'ES-11', 'unidad', 'Kit Beta · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', false, '2026-11-12 14:57:52.384574', '2026-11-12 14:57:52.728751', 344.177, true);
INSERT INTO public.change_events VALUES (55, 'ed9d747dc9bbeaa0c157a14ecc44fad33fc27d926074942fc7726a6775b68c3a', 5, 56, 'CENTRO', 'CE-21', 'entrada', 'Foro Atlas · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T03:00:00"}', false, '2026-11-12 14:57:52.4355', '2026-11-12 14:57:52.812951', 377.45099999999996, true);
INSERT INTO public.change_events VALUES (56, '7c60978617eb75d0d022742037f282827f9bf524dc7deabeed9f480f4a490e8d', 5, 57, 'CENTRO', 'CE-22', 'entrada', 'Foro Atlas · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T06:00:00"}', false, '2026-11-12 14:57:52.437546', '2026-11-12 14:57:52.812951', 375.405, true);
INSERT INTO public.change_events VALUES (57, '5befd356657c682c0fc6819263fe83e6cc769999fe4301061e21d82c88852a84', 5, 58, 'CENTRO', 'CE-30', 'entrada', 'Muestra Orión · sesión 1', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-17T00:00:00"}', false, '2026-11-12 14:57:52.440517', '2026-11-12 14:57:52.812951', 372.43399999999997, true);
INSERT INTO public.change_events VALUES (58, 'dfbfdfcdc79525515bd3082bbd12a521bbc26c473dc5870a69f8eb56d0a3cf15', 5, 59, 'CENTRO', 'CE-31', 'entrada', 'Muestra Orión · sesión 2', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', false, '2026-11-12 14:57:52.442671', '2026-11-12 14:57:52.812951', 370.28, true);
INSERT INTO public.change_events VALUES (59, 'd31aab86d669faa0dc789eadc3a3fc03b8e5dd5c58a02b3e7266a61df9c8fac4', 5, 60, 'CENTRO', 'CE-32', 'entrada', 'Muestra Orión · sesión 3', 'NUEVO_RECURSO', '{}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-17T06:00:00"}', false, '2026-11-12 14:57:52.444617', '2026-11-12 14:57:52.812951', 368.334, true);
INSERT INTO public.change_events VALUES (60, '7def22690ab7e19478996b8af68bd33e04311d9032ccc6b8d393a26136e79c72', 4, 43, 'OESTE', 'OE-20', 'turno', 'Turno Taller · sesión 1', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-16T00:00:00"}', '{"available": 3, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-16T00:00:00"}', true, '2026-11-12 14:57:53.768032', '2026-11-12 14:57:53.820446', 52.414, true);
INSERT INTO public.change_events VALUES (61, '48128ff94f38915d29b397937fcf80cbbef3de1bf428edadb0036e01ef088fa7', 5, 50, 'CENTRO', 'CE-01', 'entrada', 'Feria Nébula · sesión 2', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', '{"available": 2, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', true, '2026-11-12 14:57:53.978595', '2026-11-12 14:57:54.031166', 52.571, true);
INSERT INTO public.change_events VALUES (62, '36c52ab06477c2daa854797eb2d9440789f5f5a92e6e3bcd4de3daa56fd6acec', 4, 37, 'OESTE', 'OE-00', 'turno', 'Turno Consulta A · sesión 1', 'AGOTADO', '{"available": 1, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T00:00:00"}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T00:00:00", "conflicto_retenidas": 1}', false, '2026-11-12 14:57:54.184681', '2026-11-12 14:57:54.221498', 36.817, true);
INSERT INTO public.change_events VALUES (63, '98335ac096904ea0c5b909c597e0e6c51e2f84e57fb92844e1facab3f34c1311', 4, 38, 'OESTE', 'OE-01', 'turno', 'Turno Consulta A · sesión 2', 'CANCELACION', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', '{"available": 0, "capacity": 8, "status": "CANCELADO", "starts_at": "2026-11-14T03:00:00"}', true, '2026-11-12 14:57:54.173677', '2026-11-12 14:57:54.221498', 47.821000000000005, true);
INSERT INTO public.change_events VALUES (64, '96242e9ac4942e06e1d0f90d438d09abe99373cc5ebd955584b01db9de04ee2b', 4, 39, 'OESTE', 'OE-02', 'turno', 'Turno Consulta A · sesión 3', 'REDUCCION', '{"available": 2, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', '{"available": 1, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00", "conflicto_retenidas": 2}', false, '2026-11-12 14:57:54.178017', '2026-11-12 14:57:54.221498', 43.481, true);
INSERT INTO public.change_events VALUES (65, '4f9cdc2bbb0afdde7ed12d694150e29190fa292e82dd22b6b9d72be84d22c9a5', 5, 50, 'CENTRO', 'CE-01', 'entrada', 'Feria Nébula · sesión 2', 'AGOTADO', '{"available": 2, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00", "conflicto_retenidas": 2}', false, '2026-11-12 14:57:54.181736', '2026-11-12 14:57:54.237673', 55.937, true);
INSERT INTO public.change_events VALUES (66, 'e0d2b3620915baf8d02ff345bd42e5d578f2602f07cf4c38a394bec815e4a393', 1, 2, 'NORTE', 'NO-01', 'entrada', 'Concierto Aurora · sesión 2', 'CAMBIO_HORARIO', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', true, '2026-11-12 14:57:54.380507', '2026-11-12 14:57:54.387288', 6.781, true);
INSERT INTO public.change_events VALUES (67, 'b51d2425db5102037e92ba9d69f20a77bd8a45e91947d8e44382f78510a508d0', 1, 8, 'NORTE', 'NO-21', 'entrada', 'Gala Cometa · sesión 2', 'CANCELACION', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-16T03:00:00"}', '{"available": 0, "capacity": 10, "status": "CANCELADO", "starts_at": "2026-11-16T03:00:00"}', true, '2026-11-12 14:57:54.382972', '2026-11-12 14:57:54.387288', 4.316000000000001, true);
INSERT INTO public.change_events VALUES (68, '706dea7a4328dddadc045f1e9db5bd090f7ff63fc381e39160417aa51a86c76b', 2, 22, 'SUR', 'SU-30', 'mesa', 'Mesa Barra · sesión 1', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-17T00:00:00"}', '{"available": 2, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-17T00:00:00"}', true, '2026-11-12 14:57:54.350856', '2026-11-12 14:57:54.400828', 49.972, true);
INSERT INTO public.change_events VALUES (69, 'c91f2a954bef09ff46036989562935a0007d4bd3abecaeb0d4497ab9fec61d58', 2, 23, 'SUR', 'SU-31', 'mesa', 'Mesa Barra · sesión 2', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', '{"available": 2, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', true, '2026-11-12 14:57:54.377565', '2026-11-12 14:57:54.400828', 23.262999999999998, true);
INSERT INTO public.change_events VALUES (70, '4b979088f2468edb10cf07ce2084bc4b8970a0ab62dcaeca941f8d04963dae9d', 3, 26, 'ESTE', 'ES-01', 'unidad', 'Kit Alfa · sesión 2', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', '{"available": 1, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-14T03:00:00"}', true, '2026-11-12 14:57:54.35996', '2026-11-12 14:57:54.411055', 51.095, true);
INSERT INTO public.change_events VALUES (71, 'b7c509b5e40412a542ae8e3d8b12847f03e63b36670a5cd8dc3c0d1bdca113c8', 3, 27, 'ESTE', 'ES-02', 'unidad', 'Kit Alfa · sesión 3', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', '{"available": 1, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', true, '2026-11-12 14:57:54.367792', '2026-11-12 14:57:54.411055', 43.263000000000005, true);
INSERT INTO public.change_events VALUES (72, 'fb78485bf66a36ce25a694b64775c26c730f4ab0791c18b41d7f6765a511a77e', 3, 30, 'ESTE', 'ES-12', 'unidad', 'Kit Beta · sesión 3', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', '{"available": 1, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', true, '2026-11-12 14:57:54.374574', '2026-11-12 14:57:54.411055', 36.481, true);
INSERT INTO public.change_events VALUES (73, 'bb75b507e16363966cc6a168f05d0d6685574dc1e4f5f9f678e74bc7fcd35ce2', 3, 35, 'ESTE', 'ES-31', 'unidad', 'Kit Delta · sesión 2', 'CAMBIO_HORARIO', '{"available": 3, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', '{"available": 3, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-17T01:00:00"}', true, '2026-11-12 14:57:54.347779', '2026-11-12 14:57:54.411055', 63.275999999999996, true);
INSERT INTO public.change_events VALUES (74, 'c61bbca54d978aa7be5e479530a669ba417bacc3faa20de18eac638663158a9a', 4, 41, 'OESTE', 'OE-11', 'turno', 'Turno Consulta B · sesión 2', 'NUEVA_DISPONIBILIDAD', '{"available": 0, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', '{"available": 2, "capacity": 6, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', true, '2026-11-12 14:57:54.356604', '2026-11-12 14:57:54.426288', 69.684, true);
INSERT INTO public.change_events VALUES (75, '418a9e9b99302b80106a8321c3fe9dcf3ed9fa49925d238223ddb905e7f359ba', 5, 54, 'CENTRO', 'CE-12', 'entrada', 'Expo Prisma · sesión 3', 'CAMBIO_HORARIO', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T06:00:00"}', '{"available": 0, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T09:00:00"}', true, '2026-11-12 14:57:54.362673', '2026-11-12 14:57:54.436141', 73.468, true);
INSERT INTO public.change_events VALUES (76, 'b7268aaa300f08c9dcf769ef6ca07854d04912be5d44d34b6604dfccdf9eb3fc', 5, 57, 'CENTRO', 'CE-22', 'entrada', 'Foro Atlas · sesión 3', 'CANCELACION', '{"available": 0, "capacity": 2, "status": "ACTIVO", "starts_at": "2026-11-16T06:00:00"}', '{"available": 0, "capacity": 2, "status": "CANCELADO", "starts_at": "2026-11-16T06:00:00"}', true, '2026-11-12 14:57:54.353443', '2026-11-12 14:57:54.436141', 82.698, true);
INSERT INTO public.change_events VALUES (77, '81020ddf20668e2c4afa5898493b6faa2cf403dac38c3d8b1be38a48fbf03a0a', 2, 15, 'SUR', 'SU-02', 'mesa', 'Mesa Terraza · sesión 3', 'REAPARICION', '{"available": 0, "capacity": 4, "status": "CANCELADO", "starts_at": "2026-11-14T06:00:00"}', '{"available": 1, "capacity": 4, "status": "ACTIVO", "starts_at": "2026-11-14T06:00:00"}', true, '2026-11-12 14:57:55.014382', '2026-11-12 14:57:55.053605', 39.223, true);
INSERT INTO public.change_events VALUES (78, '48ad0f997f988bc2f83eb9d62e6375daf12232f908b3d933dca6abe0d96c77c1', 2, 17, 'SUR', 'SU-11', 'mesa', 'Mesa Salón · sesión 2', 'AUMENTO', '{"available": 1, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', '{"available": 4, "capacity": 10, "status": "ACTIVO", "starts_at": "2026-11-15T03:00:00"}', true, '2026-11-12 14:57:55.026233', '2026-11-12 14:57:55.053605', 27.372, true);
INSERT INTO public.change_events VALUES (79, '1aac16873019a5ff6d77a121305c81501b100ef828c2358a8f92f9ed4ed0a903', 2, 23, 'SUR', 'SU-31', 'mesa', 'Mesa Barra · sesión 2', 'CANCELACION', '{"available": 2, "capacity": 8, "status": "ACTIVO", "starts_at": "2026-11-17T03:00:00"}', '{"available": 0, "capacity": 8, "status": "CANCELADO", "starts_at": "2026-11-17T03:00:00", "conflicto_retenidas": 1}', true, '2026-11-12 14:57:55.035463', '2026-11-12 14:57:55.053605', 18.142, true);


--
-- Data for Name: channels; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.channels VALUES (1, 'Panel', 'panel', '{}', true);
INSERT INTO public.channels VALUES (2, 'Registro', 'registro', '{}', true);
INSERT INTO public.channels VALUES (3, 'Simulado', 'simulado', '{"fail_rate": 0.0}', true);
INSERT INTO public.channels VALUES (4, 'Webhook local', 'webhook', '{"url": "http://127.0.0.1:8000/api-prueba/webhook"}', false);
INSERT INTO public.channels VALUES (5, 'Telegram', 'telegram', '{}', false);
INSERT INTO public.channels VALUES (6, 'Correo', 'correo', '{}', false);


--
-- Data for Name: deliveries; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.deliveries VALUES (1, 1, 1, 'ENVIADA', 1, '2026-11-12 14:57:52.982271', NULL, '2026-11-12 14:57:53.475571', 493.3);
INSERT INTO public.deliveries VALUES (2, 1, 2, 'ENVIADA', 1, '2026-11-12 14:57:52.982271', NULL, '2026-11-12 14:57:53.475571', 493.3);
INSERT INTO public.deliveries VALUES (3, 1, 3, 'ENVIADA', 1, '2026-11-12 14:57:52.982271', NULL, '2026-11-12 14:57:53.475571', 493.3);
INSERT INTO public.deliveries VALUES (4, 2, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (5, 2, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (6, 2, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (7, 3, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (8, 3, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (9, 3, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (10, 4, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (11, 4, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (12, 4, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.156737', NULL, '2026-11-12 14:57:53.475571', 318.834);
INSERT INTO public.deliveries VALUES (13, 5, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.250162', NULL, '2026-11-12 14:57:53.475571', 225.409);
INSERT INTO public.deliveries VALUES (14, 5, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.250162', NULL, '2026-11-12 14:57:53.475571', 225.409);
INSERT INTO public.deliveries VALUES (15, 5, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.250162', NULL, '2026-11-12 14:57:53.475571', 225.409);
INSERT INTO public.deliveries VALUES (16, 6, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.286353', NULL, '2026-11-12 14:57:53.475571', 189.218);
INSERT INTO public.deliveries VALUES (17, 6, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.286353', NULL, '2026-11-12 14:57:53.475571', 189.218);
INSERT INTO public.deliveries VALUES (18, 6, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.286353', NULL, '2026-11-12 14:57:53.475571', 189.218);
INSERT INTO public.deliveries VALUES (19, 7, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.286353', NULL, '2026-11-12 14:57:53.475571', 189.218);
INSERT INTO public.deliveries VALUES (20, 7, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.286353', NULL, '2026-11-12 14:57:53.475571', 189.218);
INSERT INTO public.deliveries VALUES (21, 7, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.286353', NULL, '2026-11-12 14:57:53.475571', 189.218);
INSERT INTO public.deliveries VALUES (22, 8, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.845591', NULL, '2026-11-12 14:57:53.900282', 54.690999999999995);
INSERT INTO public.deliveries VALUES (23, 8, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.845591', NULL, '2026-11-12 14:57:53.900282', 54.690999999999995);
INSERT INTO public.deliveries VALUES (24, 8, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.845591', NULL, '2026-11-12 14:57:53.900282', 54.690999999999995);
INSERT INTO public.deliveries VALUES (25, 9, 1, 'ENVIADA', 1, '2026-11-12 14:57:53.845591', NULL, '2026-11-12 14:57:53.900282', 54.690999999999995);
INSERT INTO public.deliveries VALUES (26, 9, 2, 'ENVIADA', 1, '2026-11-12 14:57:53.845591', NULL, '2026-11-12 14:57:53.900282', 54.690999999999995);
INSERT INTO public.deliveries VALUES (27, 9, 3, 'ENVIADA', 1, '2026-11-12 14:57:53.845591', NULL, '2026-11-12 14:57:53.900282', 54.690999999999995);
INSERT INTO public.deliveries VALUES (28, 10, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.04136', NULL, '2026-11-12 14:57:54.105001', 63.641000000000005);
INSERT INTO public.deliveries VALUES (29, 10, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.04136', NULL, '2026-11-12 14:57:54.105001', 63.641000000000005);
INSERT INTO public.deliveries VALUES (30, 10, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.04136', NULL, '2026-11-12 14:57:54.105001', 63.641000000000005);
INSERT INTO public.deliveries VALUES (31, 11, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.04136', NULL, '2026-11-12 14:57:54.105001', 63.641000000000005);
INSERT INTO public.deliveries VALUES (32, 11, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.04136', NULL, '2026-11-12 14:57:54.105001', 63.641000000000005);
INSERT INTO public.deliveries VALUES (33, 11, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.04136', NULL, '2026-11-12 14:57:54.105001', 63.641000000000005);
INSERT INTO public.deliveries VALUES (34, 12, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.265563', NULL, '2026-11-12 14:57:54.320325', 54.762);
INSERT INTO public.deliveries VALUES (35, 12, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.265563', NULL, '2026-11-12 14:57:54.320325', 54.762);
INSERT INTO public.deliveries VALUES (36, 12, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.265563', NULL, '2026-11-12 14:57:54.320325', 54.762);
INSERT INTO public.deliveries VALUES (37, 13, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.447504', NULL, '2026-11-12 14:57:54.672497', 224.993);
INSERT INTO public.deliveries VALUES (38, 13, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.447504', NULL, '2026-11-12 14:57:54.672497', 224.993);
INSERT INTO public.deliveries VALUES (39, 13, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.447504', NULL, '2026-11-12 14:57:54.672497', 224.993);
INSERT INTO public.deliveries VALUES (40, 14, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.454893', NULL, '2026-11-12 14:57:54.672497', 217.60399999999998);
INSERT INTO public.deliveries VALUES (41, 14, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.454893', NULL, '2026-11-12 14:57:54.672497', 217.60399999999998);
INSERT INTO public.deliveries VALUES (42, 14, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.454893', NULL, '2026-11-12 14:57:54.672497', 217.60399999999998);
INSERT INTO public.deliveries VALUES (43, 15, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.463924', NULL, '2026-11-12 14:57:54.672497', 208.573);
INSERT INTO public.deliveries VALUES (44, 15, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.463924', NULL, '2026-11-12 14:57:54.672497', 208.573);
INSERT INTO public.deliveries VALUES (45, 15, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.463924', NULL, '2026-11-12 14:57:54.672497', 208.573);
INSERT INTO public.deliveries VALUES (46, 16, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.486108', NULL, '2026-11-12 14:57:54.672497', 186.389);
INSERT INTO public.deliveries VALUES (47, 16, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.486108', NULL, '2026-11-12 14:57:54.672497', 186.389);
INSERT INTO public.deliveries VALUES (48, 16, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.486108', NULL, '2026-11-12 14:57:54.672497', 186.389);
INSERT INTO public.deliveries VALUES (49, 17, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.512238', NULL, '2026-11-12 14:57:54.672497', 160.25900000000001);
INSERT INTO public.deliveries VALUES (50, 17, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.512238', NULL, '2026-11-12 14:57:54.672497', 160.25900000000001);
INSERT INTO public.deliveries VALUES (51, 17, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.512238', NULL, '2026-11-12 14:57:54.672497', 160.25900000000001);
INSERT INTO public.deliveries VALUES (52, 18, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.533198', NULL, '2026-11-12 14:57:54.672497', 139.299);
INSERT INTO public.deliveries VALUES (53, 18, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.533198', NULL, '2026-11-12 14:57:54.672497', 139.299);
INSERT INTO public.deliveries VALUES (54, 18, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.533198', NULL, '2026-11-12 14:57:54.672497', 139.299);
INSERT INTO public.deliveries VALUES (55, 19, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.548332', NULL, '2026-11-12 14:57:54.672497', 124.16499999999999);
INSERT INTO public.deliveries VALUES (56, 19, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.548332', NULL, '2026-11-12 14:57:54.672497', 124.16499999999999);
INSERT INTO public.deliveries VALUES (57, 19, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.548332', NULL, '2026-11-12 14:57:54.672497', 124.16499999999999);
INSERT INTO public.deliveries VALUES (58, 20, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.564484', NULL, '2026-11-12 14:57:54.672497', 108.01299999999999);
INSERT INTO public.deliveries VALUES (59, 20, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.564484', NULL, '2026-11-12 14:57:54.672497', 108.01299999999999);
INSERT INTO public.deliveries VALUES (60, 20, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.564484', NULL, '2026-11-12 14:57:54.672497', 108.01299999999999);
INSERT INTO public.deliveries VALUES (61, 21, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.576877', NULL, '2026-11-12 14:57:54.672497', 95.61999999999999);
INSERT INTO public.deliveries VALUES (62, 21, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.576877', NULL, '2026-11-12 14:57:54.672497', 95.61999999999999);
INSERT INTO public.deliveries VALUES (63, 21, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.576877', NULL, '2026-11-12 14:57:54.672497', 95.61999999999999);
INSERT INTO public.deliveries VALUES (64, 22, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.576877', NULL, '2026-11-12 14:57:54.672497', 95.61999999999999);
INSERT INTO public.deliveries VALUES (65, 22, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.576877', NULL, '2026-11-12 14:57:54.672497', 95.61999999999999);
INSERT INTO public.deliveries VALUES (66, 22, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.576877', NULL, '2026-11-12 14:57:54.672497', 95.61999999999999);
INSERT INTO public.deliveries VALUES (67, 23, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.627224', NULL, '2026-11-12 14:57:54.672497', 45.273);
INSERT INTO public.deliveries VALUES (68, 23, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.627224', NULL, '2026-11-12 14:57:54.672497', 45.273);
INSERT INTO public.deliveries VALUES (69, 23, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.627224', NULL, '2026-11-12 14:57:54.672497', 45.273);
INSERT INTO public.deliveries VALUES (70, 24, 1, 'ENVIADA', 1, '2026-11-12 14:57:54.637928', NULL, '2026-11-12 14:57:54.672497', 34.569);
INSERT INTO public.deliveries VALUES (71, 24, 2, 'ENVIADA', 1, '2026-11-12 14:57:54.637928', NULL, '2026-11-12 14:57:54.672497', 34.569);
INSERT INTO public.deliveries VALUES (72, 24, 3, 'ENVIADA', 1, '2026-11-12 14:57:54.637928', NULL, '2026-11-12 14:57:54.672497', 34.569);
INSERT INTO public.deliveries VALUES (73, 25, 1, 'ENVIADA', 1, '2026-11-12 14:57:55.095575', NULL, '2026-11-12 14:57:55.19211', 96.535);
INSERT INTO public.deliveries VALUES (74, 25, 2, 'ENVIADA', 1, '2026-11-12 14:57:55.095575', NULL, '2026-11-12 14:57:55.19211', 96.535);
INSERT INTO public.deliveries VALUES (75, 25, 3, 'ENVIADA', 1, '2026-11-12 14:57:55.095575', NULL, '2026-11-12 14:57:55.19211', 96.535);
INSERT INTO public.deliveries VALUES (76, 26, 1, 'ENVIADA', 1, '2026-11-12 14:57:55.10931', NULL, '2026-11-12 14:57:55.19211', 82.8);
INSERT INTO public.deliveries VALUES (77, 26, 2, 'ENVIADA', 1, '2026-11-12 14:57:55.10931', NULL, '2026-11-12 14:57:55.19211', 82.8);
INSERT INTO public.deliveries VALUES (78, 26, 3, 'ENVIADA', 1, '2026-11-12 14:57:55.10931', NULL, '2026-11-12 14:57:55.19211', 82.8);
INSERT INTO public.deliveries VALUES (79, 27, 1, 'ENVIADA', 1, '2026-11-12 14:57:55.123761', NULL, '2026-11-12 14:57:55.19211', 68.34899999999999);
INSERT INTO public.deliveries VALUES (80, 27, 2, 'ENVIADA', 1, '2026-11-12 14:57:55.123761', NULL, '2026-11-12 14:57:55.19211', 68.34899999999999);
INSERT INTO public.deliveries VALUES (81, 27, 3, 'ENVIADA', 1, '2026-11-12 14:57:55.123761', NULL, '2026-11-12 14:57:55.19211', 68.34899999999999);
INSERT INTO public.deliveries VALUES (82, 28, 1, 'ENVIADA', 1, '2026-11-12 14:57:55.147786', NULL, '2026-11-12 14:57:55.19211', 44.324000000000005);
INSERT INTO public.deliveries VALUES (83, 28, 2, 'ENVIADA', 1, '2026-11-12 14:57:55.147786', NULL, '2026-11-12 14:57:55.19211', 44.324000000000005);
INSERT INTO public.deliveries VALUES (84, 28, 3, 'ENVIADA', 1, '2026-11-12 14:57:55.147786', NULL, '2026-11-12 14:57:55.19211', 44.324000000000005);


--
-- Data for Name: notification_log; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.notification_log VALUES (1, '2026-11-12 14:57:53.475571', 1, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (2, '2026-11-12 14:57:53.475571', 1, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (3, '2026-11-12 14:57:53.475571', 1, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (4, '2026-11-12 14:57:53.475571', 2, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (5, '2026-11-12 14:57:53.475571', 2, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (6, '2026-11-12 14:57:53.475571', 2, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (7, '2026-11-12 14:57:53.475571', 3, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (8, '2026-11-12 14:57:53.475571', 3, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (9, '2026-11-12 14:57:53.475571', 3, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (10, '2026-11-12 14:57:53.475571', 4, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (11, '2026-11-12 14:57:53.475571', 4, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (12, '2026-11-12 14:57:53.475571', 4, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (13, '2026-11-12 14:57:53.475571', 5, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (14, '2026-11-12 14:57:53.475571', 5, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (15, '2026-11-12 14:57:53.475571', 5, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (16, '2026-11-12 14:57:53.475571', 6, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (17, '2026-11-12 14:57:53.475571', 6, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (18, '2026-11-12 14:57:53.475571', 6, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (19, '2026-11-12 14:57:53.475571', 7, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (20, '2026-11-12 14:57:53.475571', 7, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (21, '2026-11-12 14:57:53.475571', 7, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (22, '2026-11-12 14:57:53.900282', 8, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (23, '2026-11-12 14:57:53.900282', 8, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (24, '2026-11-12 14:57:53.900282', 8, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (25, '2026-11-12 14:57:53.900282', 9, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (26, '2026-11-12 14:57:53.900282', 9, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (27, '2026-11-12 14:57:53.900282', 9, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (28, '2026-11-12 14:57:54.105001', 10, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (29, '2026-11-12 14:57:54.105001', 10, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (30, '2026-11-12 14:57:54.105001', 10, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (31, '2026-11-12 14:57:54.105001', 11, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (32, '2026-11-12 14:57:54.105001', 11, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (33, '2026-11-12 14:57:54.105001', 11, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (34, '2026-11-12 14:57:54.320325', 12, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (35, '2026-11-12 14:57:54.320325', 12, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (36, '2026-11-12 14:57:54.320325', 12, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (37, '2026-11-12 14:57:54.672497', 13, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (38, '2026-11-12 14:57:54.672497', 13, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (39, '2026-11-12 14:57:54.672497', 13, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (40, '2026-11-12 14:57:54.672497', 14, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (41, '2026-11-12 14:57:54.672497', 14, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (42, '2026-11-12 14:57:54.672497', 14, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (43, '2026-11-12 14:57:54.672497', 15, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (44, '2026-11-12 14:57:54.672497', 15, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (45, '2026-11-12 14:57:54.672497', 15, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (46, '2026-11-12 14:57:54.672497', 16, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (47, '2026-11-12 14:57:54.672497', 16, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (48, '2026-11-12 14:57:54.672497', 16, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (49, '2026-11-12 14:57:54.672497', 17, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (50, '2026-11-12 14:57:54.672497', 17, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (51, '2026-11-12 14:57:54.672497', 17, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (52, '2026-11-12 14:57:54.672497', 18, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (53, '2026-11-12 14:57:54.672497', 18, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (54, '2026-11-12 14:57:54.672497', 18, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (55, '2026-11-12 14:57:54.672497', 19, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (56, '2026-11-12 14:57:54.672497', 19, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (57, '2026-11-12 14:57:54.672497', 19, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (58, '2026-11-12 14:57:54.672497', 20, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (59, '2026-11-12 14:57:54.672497', 20, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (60, '2026-11-12 14:57:54.672497', 20, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (61, '2026-11-12 14:57:54.672497', 21, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (62, '2026-11-12 14:57:54.672497', 21, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (63, '2026-11-12 14:57:54.672497', 21, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (64, '2026-11-12 14:57:54.672497', 22, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (65, '2026-11-12 14:57:54.672497', 22, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (66, '2026-11-12 14:57:54.672497', 22, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (67, '2026-11-12 14:57:54.672497', 23, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (68, '2026-11-12 14:57:54.672497', 23, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (69, '2026-11-12 14:57:54.672497', 23, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (70, '2026-11-12 14:57:54.672497', 24, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (71, '2026-11-12 14:57:54.672497', 24, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (72, '2026-11-12 14:57:54.672497', 24, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (73, '2026-11-12 14:57:55.19211', 25, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (74, '2026-11-12 14:57:55.19211', 25, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (75, '2026-11-12 14:57:55.19211', 25, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (76, '2026-11-12 14:57:55.19211', 26, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (77, '2026-11-12 14:57:55.19211', 26, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (78, '2026-11-12 14:57:55.19211', 26, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (79, '2026-11-12 14:57:55.19211', 27, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (80, '2026-11-12 14:57:55.19211', 27, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (81, '2026-11-12 14:57:55.19211', 27, 'Simulado', 'OK', 'recibida por canal simulado');
INSERT INTO public.notification_log VALUES (82, '2026-11-12 14:57:55.19211', 28, 'Panel', 'OK', 'visible en panel');
INSERT INTO public.notification_log VALUES (83, '2026-11-12 14:57:55.19211', 28, 'Registro', 'OK', 'escrita en notificaciones.log');
INSERT INTO public.notification_log VALUES (84, '2026-11-12 14:57:55.19211', 28, 'Simulado', 'OK', 'recibida por canal simulado');


--
-- Data for Name: operators; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.operators VALUES (1, 'Operador 01', NULL, '["NORTE"]', true, 3, 0, NULL);
INSERT INTO public.operators VALUES (6, 'Operador 06 (todas)', NULL, '[]', true, 3, 3, '2026-11-12 14:57:53.845591');
INSERT INTO public.operators VALUES (5, 'Operador 05', NULL, '["CENTRO"]', true, 3, 0, '2026-11-12 14:57:54.04136');
INSERT INTO public.operators VALUES (3, 'Operador 03', NULL, '["ESTE"]', true, 3, 3, '2026-11-12 14:57:54.512238');
INSERT INTO public.operators VALUES (4, 'Operador 04', NULL, '["OESTE"]', true, 3, 3, '2026-11-12 14:57:54.576877');
INSERT INTO public.operators VALUES (2, 'Operador 02', NULL, '["SUR"]', true, 3, 3, '2026-11-12 14:57:55.147786');


--
-- Data for Name: poll_log; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.poll_log VALUES (1, 1, '2026-11-12 14:57:52.631692', 1.814779000000044, 'OK', 1, 12, 12, 0, 0, NULL);
INSERT INTO public.poll_log VALUES (2, 2, '2026-11-12 14:57:52.684903', 2.295758000002479, 'OK', 1, 12, 11, 0, 0, NULL);
INSERT INTO public.poll_log VALUES (3, 3, '2026-11-12 14:57:52.728751', 1.588448999996217, 'OK', 1, 12, 12, 0, 0, NULL);
INSERT INTO public.poll_log VALUES (4, 4, '2026-11-12 14:57:52.773128', 3.2934609999983877, 'OK', 1, 12, 12, 0, 0, NULL);
INSERT INTO public.poll_log VALUES (5, 5, '2026-11-12 14:57:52.812951', 2.875926999990952, 'OK', 1, 12, 12, 0, 0, NULL);
INSERT INTO public.poll_log VALUES (6, 1, '2026-11-12 14:57:53.781082', 2.5336559999971087, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (7, 2, '2026-11-12 14:57:53.798128', 2.616603999996414, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (8, 3, '2026-11-12 14:57:53.809148', 1.4148099999999886, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (9, 4, '2026-11-12 14:57:53.820446', 1.5616759999943497, 'OK', 1, 12, 1, 0, 11, NULL);
INSERT INTO public.poll_log VALUES (10, 5, '2026-11-12 14:57:53.836177', 1.5033219999907033, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (11, 1, '2026-11-12 14:57:53.985104', 1.9836010000062743, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (12, 2, '2026-11-12 14:57:53.998096', 1.976675000008754, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (13, 3, '2026-11-12 14:57:54.010239', 1.797602999999981, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (14, 4, '2026-11-12 14:57:54.020322', 1.4173360000029334, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (15, 5, '2026-11-12 14:57:54.031166', 1.302506000001813, 'OK', 1, 12, 1, 0, 11, NULL);
INSERT INTO public.poll_log VALUES (16, 1, '2026-11-12 14:57:54.190488', 1.510158000002093, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (17, 2, '2026-11-12 14:57:54.201411', 1.1591809999913494, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (18, 3, '2026-11-12 14:57:54.212251', 2.2651120000034553, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (19, 4, '2026-11-12 14:57:54.221498', 1.2017649999904734, 'OK', 1, 12, 3, 0, 9, NULL);
INSERT INTO public.poll_log VALUES (20, 5, '2026-11-12 14:57:54.237673', 1.057819999999765, 'OK', 1, 12, 1, 0, 11, NULL);
INSERT INTO public.poll_log VALUES (21, 1, '2026-11-12 14:57:54.387288', 1.279134000014892, 'OK', 1, 12, 2, 0, 10, NULL);
INSERT INTO public.poll_log VALUES (22, 2, '2026-11-12 14:57:54.400828', 1.0124469999936991, 'OK', 1, 12, 2, 0, 10, NULL);
INSERT INTO public.poll_log VALUES (23, 3, '2026-11-12 14:57:54.411055', 0.9250069999779953, 'OK', 1, 12, 4, 0, 8, NULL);
INSERT INTO public.poll_log VALUES (24, 4, '2026-11-12 14:57:54.426288', 1.1137980000057723, 'OK', 1, 12, 1, 0, 11, NULL);
INSERT INTO public.poll_log VALUES (25, 5, '2026-11-12 14:57:54.436141', 1.1749120000104085, 'OK', 1, 12, 2, 0, 10, NULL);
INSERT INTO public.poll_log VALUES (26, 1, '2026-11-12 14:57:55.041365', 2.00064399999178, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (27, 2, '2026-11-12 14:57:55.053605', 1.6057040000134748, 'OK', 1, 12, 3, 0, 9, NULL);
INSERT INTO public.poll_log VALUES (28, 3, '2026-11-12 14:57:55.071031', 1.4279800000167597, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (29, 4, '2026-11-12 14:57:55.078943', 0.9604899999828831, 'OK', 1, 12, 0, 0, 12, NULL);
INSERT INTO public.poll_log VALUES (30, 5, '2026-11-12 14:57:55.087052', 1.1974479999992127, 'OK', 1, 12, 0, 0, 12, NULL);


--
-- Data for Name: regions; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.regions VALUES ('NORTE', 'Región Norte (ficticia)');
INSERT INTO public.regions VALUES ('SUR', 'Región Sur (ficticia)');
INSERT INTO public.regions VALUES ('ESTE', 'Región Este (ficticia)');
INSERT INTO public.regions VALUES ('OESTE', 'Región Oeste (ficticia)');
INSERT INTO public.regions VALUES ('CENTRO', 'Región Centro (ficticia)');


--
-- Data for Name: request_events; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.request_events VALUES (1, 36, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (2, 37, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (3, 34, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (4, 38, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (5, 40, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (6, 33, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (7, 39, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (8, 35, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (9, 24, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (10, 17, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (11, 20, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (12, 22, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (13, 23, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (14, 18, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (15, 21, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (16, 19, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (17, 1, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (18, 5, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (19, 7, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (20, 3, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (21, 8, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (22, 4, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (23, 6, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (24, 2, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (25, 27, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (26, 25, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (27, 26, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (28, 29, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (29, 30, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (30, 28, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (31, 32, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (32, 31, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (33, 16, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (34, 11, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (35, 15, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (36, 12, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (37, 10, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (38, 13, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (39, 14, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (40, 9, '2026-11-12 14:57:52.565632', 'PENDIENTE', 'ELEGIBLE', 'motor', 'condiciones validadas');
INSERT INTO public.request_events VALUES (41, 11, '2026-11-12 14:57:52.982271', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=17 operador=2');
INSERT INTO public.request_events VALUES (42, 24, '2026-11-12 14:57:53.156737', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=35 operador=3');
INSERT INTO public.request_events VALUES (43, 17, '2026-11-12 14:57:53.156737', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=35 operador=6');
INSERT INTO public.request_events VALUES (44, 20, '2026-11-12 14:57:53.156737', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=35 operador=3');
INSERT INTO public.request_events VALUES (45, 27, '2026-11-12 14:57:53.250162', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=37 operador=4');
INSERT INTO public.request_events VALUES (46, 25, '2026-11-12 14:57:53.286353', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=39 operador=6');
INSERT INTO public.request_events VALUES (47, 26, '2026-11-12 14:57:53.286353', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=39 operador=4');
INSERT INTO public.request_events VALUES (48, 29, '2026-11-12 14:57:53.845591', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=43 operador=4');
INSERT INTO public.request_events VALUES (49, 30, '2026-11-12 14:57:53.845591', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=43 operador=6');
INSERT INTO public.request_events VALUES (50, 36, '2026-11-12 14:57:54.04136', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=50 operador=5');
INSERT INTO public.request_events VALUES (51, 37, '2026-11-12 14:57:54.04136', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=50 operador=5');
INSERT INTO public.request_events VALUES (52, 27, '2026-11-12 14:57:54.245967', 'ASIGNADO', 'REINTENTO', 'motor', 'recurso ya no disponible');
INSERT INTO public.request_events VALUES (53, 26, '2026-11-12 14:57:54.275059', 'ASIGNADO', 'REINTENTO', 'motor', 'recurso ya no disponible');
INSERT INTO public.request_events VALUES (54, 37, '2026-11-12 14:57:54.290461', 'ASIGNADO', 'REINTENTO', 'motor', 'recurso ya no disponible');
INSERT INTO public.request_events VALUES (55, 36, '2026-11-12 14:57:54.290461', 'ASIGNADO', 'REINTENTO', 'motor', 'recurso ya no disponible');
INSERT INTO public.request_events VALUES (56, 16, '2026-11-12 14:57:54.463924', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=22 operador=2');
INSERT INTO public.request_events VALUES (57, 15, '2026-11-12 14:57:54.486108', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=23 operador=2');
INSERT INTO public.request_events VALUES (58, 22, '2026-11-12 14:57:54.512238', 'ELEGIBLE', 'ASIGNADO', 'motor', 'slot=26 operador=3');
INSERT INTO public.request_events VALUES (59, 27, '2026-11-12 14:57:54.576877', 'REINTENTO', 'ASIGNADO', 'motor', 'slot=41 operador=4');
INSERT INTO public.request_events VALUES (60, 26, '2026-11-12 14:57:54.576877', 'REINTENTO', 'ASIGNADO', 'motor', 'slot=41 operador=4');
INSERT INTO public.request_events VALUES (61, 15, '2026-11-12 14:57:55.123761', 'ASIGNADO', 'REINTENTO', 'motor', 'recurso ya no disponible');
INSERT INTO public.request_events VALUES (62, 15, '2026-11-12 14:57:55.147786', 'REINTENTO', 'ASIGNADO', 'motor', 'slot=15 operador=2');


--
-- Data for Name: requests; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.requests VALUES (1, 'SOL-0001', 'Expediente Jaguar 001', 'NORTE', 'entrada', 1, 2, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 10:12:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (2, 'SOL-0002', 'Expediente Jade 002', 'NORTE', 'entrada', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 12:10:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (3, 'SOL-0003', 'Expediente Nopal 003', 'NORTE', 'entrada', 3, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 10:23:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (4, 'SOL-0004', 'Expediente Ceiba 004', 'NORTE', 'entrada', 4, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 05:50:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (5, 'SOL-0005', 'Expediente Jaguar 005', 'NORTE', 'entrada', 2, 2, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 08:28:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (6, 'SOL-0006', 'Expediente Colibrí 006', 'NORTE', 'entrada', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 09:24:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (7, 'SOL-0007', 'Expediente Lince 007', 'NORTE', 'entrada', 2, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 09:33:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (8, 'SOL-0008', 'Expediente Ámbar 008', 'NORTE', 'entrada', 3, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 11:20:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (9, 'SOL-0009', 'Expediente Cobre 009', 'SUR', 'mesa', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 11:19:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (10, 'SOL-0010', 'Expediente Jade 010', 'SUR', 'mesa', 3, 2, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 12:30:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (12, 'SOL-0012', 'Expediente Ópalo 012', 'SUR', 'mesa', 3, 2, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 04:59:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (13, 'SOL-0013', 'Expediente Ámbar 013', 'SUR', 'mesa', 3, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 12:35:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (14, 'SOL-0014', 'Expediente Ópalo 014', 'SUR', 'mesa', 3, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 14:08:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (18, 'SOL-0018', 'Expediente Lince 018', 'ESTE', 'unidad', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 05:47:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (19, 'SOL-0019', 'Expediente Colibrí 019', 'ESTE', 'unidad', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 13:02:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (21, 'SOL-0021', 'Expediente Lince 021', 'ESTE', 'unidad', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 06:24:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (23, 'SOL-0023', 'Expediente Ópalo 023', 'ESTE', 'unidad', 4, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 12:20:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (11, 'SOL-0011', 'Expediente Colibrí 011', 'SUR', 'mesa', 2, 1, '{}', 'ASIGNADO', 2, 1, NULL, '2026-11-12 05:22:52.446389', '2026-11-12 14:57:52.982271');
INSERT INTO public.requests VALUES (17, 'SOL-0017', 'Expediente Cobre 017', 'ESTE', 'unidad', 3, 1, '{}', 'ASIGNADO', 6, 1, NULL, '2026-11-12 05:30:52.446389', '2026-11-12 14:57:53.156737');
INSERT INTO public.requests VALUES (20, 'SOL-0020', 'Expediente Colibrí 020', 'ESTE', 'unidad', 3, 1, '{}', 'ASIGNADO', 3, 1, NULL, '2026-11-12 07:12:52.446389', '2026-11-12 14:57:53.156737');
INSERT INTO public.requests VALUES (16, 'SOL-0016', 'Expediente Cobre 016', 'SUR', 'mesa', 1, 2, '{}', 'ASIGNADO', 2, 1, NULL, '2026-11-12 08:26:52.446389', '2026-11-12 14:57:54.463924');
INSERT INTO public.requests VALUES (22, 'SOL-0022', 'Expediente Quetzal 022', 'ESTE', 'unidad', 4, 1, '{}', 'ASIGNADO', 3, 1, NULL, '2026-11-12 09:51:52.446389', '2026-11-12 14:57:54.512238');
INSERT INTO public.requests VALUES (15, 'SOL-0015', 'Expediente Ceiba 015', 'SUR', 'mesa', 2, 1, '{}', 'ASIGNADO', 2, 2, NULL, '2026-11-12 07:44:52.446389', '2026-11-12 14:57:55.147786');
INSERT INTO public.requests VALUES (28, 'SOL-0028', 'Expediente Ceiba 028', 'OESTE', 'turno', 4, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 12:45:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (31, 'SOL-0031', 'Expediente Ópalo 031', 'OESTE', 'turno', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 09:37:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (32, 'SOL-0032', 'Expediente Ámbar 032', 'OESTE', 'turno', 5, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 07:28:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (33, 'SOL-0033', 'Expediente Ópalo 033', 'CENTRO', 'entrada', 3, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 10:43:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (34, 'SOL-0034', 'Expediente Jaguar 034', 'CENTRO', 'entrada', 1, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 14:35:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (35, 'SOL-0035', 'Expediente Cobre 035', 'CENTRO', 'entrada', 4, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 11:11:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (38, 'SOL-0038', 'Expediente Ópalo 038', 'CENTRO', 'entrada', 2, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 06:39:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (39, 'SOL-0039', 'Expediente Jaguar 039', 'CENTRO', 'entrada', 4, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 05:12:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (40, 'SOL-0040', 'Expediente Cobre 040', 'CENTRO', 'entrada', 3, 1, '{}', 'ELEGIBLE', NULL, 0, NULL, '2026-11-12 06:52:52.446389', '2026-11-12 14:57:52.565632');
INSERT INTO public.requests VALUES (24, 'SOL-0024', 'Expediente Nopal 024', 'ESTE', 'unidad', 2, 1, '{}', 'ASIGNADO', 3, 1, NULL, '2026-11-12 09:25:52.446389', '2026-11-12 14:57:53.156737');
INSERT INTO public.requests VALUES (25, 'SOL-0025', 'Expediente Jade 025', 'OESTE', 'turno', 1, 1, '{}', 'ASIGNADO', 6, 1, NULL, '2026-11-12 08:45:52.446389', '2026-11-12 14:57:53.286353');
INSERT INTO public.requests VALUES (29, 'SOL-0029', 'Expediente Jade 029', 'OESTE', 'turno', 4, 1, '{}', 'ASIGNADO', 4, 1, NULL, '2026-11-12 10:25:52.446389', '2026-11-12 14:57:53.845591');
INSERT INTO public.requests VALUES (30, 'SOL-0030', 'Expediente Ópalo 030', 'OESTE', 'turno', 4, 2, '{}', 'ASIGNADO', 6, 1, NULL, '2026-11-12 11:20:52.446389', '2026-11-12 14:57:53.845591');
INSERT INTO public.requests VALUES (37, 'SOL-0037', 'Expediente Ceiba 037', 'CENTRO', 'entrada', 1, 1, '{}', 'REINTENTO', NULL, 1, NULL, '2026-11-12 13:44:52.446389', '2026-11-12 14:57:54.290461');
INSERT INTO public.requests VALUES (36, 'SOL-0036', 'Expediente Lince 036', 'CENTRO', 'entrada', 1, 1, '{}', 'REINTENTO', NULL, 1, NULL, '2026-11-12 11:02:52.446389', '2026-11-12 14:57:54.290461');
INSERT INTO public.requests VALUES (27, 'SOL-0027', 'Expediente Cobre 027', 'OESTE', 'turno', 1, 1, '{}', 'ASIGNADO', 4, 2, NULL, '2026-11-12 06:39:52.446389', '2026-11-12 14:57:54.576877');
INSERT INTO public.requests VALUES (26, 'SOL-0026', 'Expediente Colibrí 026', 'OESTE', 'turno', 2, 1, '{}', 'ASIGNADO', 4, 2, NULL, '2026-11-12 10:50:52.446389', '2026-11-12 14:57:54.576877');


--
-- Data for Name: settings; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: sim_items; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.sim_items VALUES (1, 'sim-norte', 'NO-00', 'Concierto Aurora · sesión 1', 'NORTE', 'entrada', '2026-11-14 00:00:00', 2, 0, false, '2026-11-12 14:57:52.326087');
INSERT INTO public.sim_items VALUES (3, 'sim-norte', 'NO-02', 'Concierto Aurora · sesión 3', 'NORTE', 'entrada', '2026-11-14 06:00:00', 6, 0, false, '2026-11-12 14:57:52.332775');
INSERT INTO public.sim_items VALUES (4, 'sim-norte', 'NO-10', 'Festival Brisa · sesión 1', 'NORTE', 'entrada', '2026-11-15 00:00:00', 4, 0, false, '2026-11-12 14:57:52.335222');
INSERT INTO public.sim_items VALUES (5, 'sim-norte', 'NO-11', 'Festival Brisa · sesión 2', 'NORTE', 'entrada', '2026-11-15 03:00:00', 4, 0, false, '2026-11-12 14:57:52.337833');
INSERT INTO public.sim_items VALUES (6, 'sim-norte', 'NO-12', 'Festival Brisa · sesión 3', 'NORTE', 'entrada', '2026-11-15 06:00:00', 4, 0, false, '2026-11-12 14:57:52.33974');
INSERT INTO public.sim_items VALUES (9, 'sim-norte', 'NO-22', 'Gala Cometa · sesión 3', 'NORTE', 'entrada', '2026-11-16 06:00:00', 2, 0, false, '2026-11-12 14:57:52.34716');
INSERT INTO public.sim_items VALUES (10, 'sim-norte', 'NO-30', 'Teatro Lumen · sesión 1', 'NORTE', 'entrada', '2026-11-17 00:00:00', 10, 0, false, '2026-11-12 14:57:52.349058');
INSERT INTO public.sim_items VALUES (11, 'sim-norte', 'NO-31', 'Teatro Lumen · sesión 2', 'NORTE', 'entrada', '2026-11-17 03:00:00', 8, 0, false, '2026-11-12 14:57:52.350932');
INSERT INTO public.sim_items VALUES (12, 'sim-norte', 'NO-32', 'Teatro Lumen · sesión 3', 'NORTE', 'entrada', '2026-11-17 06:00:00', 2, 0, false, '2026-11-12 14:57:52.352881');
INSERT INTO public.sim_items VALUES (16, 'sim-sur', 'SU-10', 'Mesa Salón · sesión 1', 'SUR', 'mesa', '2026-11-15 00:00:00', 4, 0, false, '2026-11-12 14:57:52.360763');
INSERT INTO public.sim_items VALUES (18, 'sim-sur', 'SU-12', 'Mesa Salón · sesión 3', 'SUR', 'mesa', '2026-11-15 06:00:00', 10, 0, false, '2026-11-12 14:57:52.365328');
INSERT INTO public.sim_items VALUES (19, 'sim-sur', 'SU-20', 'Mesa Jardín · sesión 1', 'SUR', 'mesa', '2026-11-16 00:00:00', 2, 0, false, '2026-11-12 14:57:52.367245');
INSERT INTO public.sim_items VALUES (21, 'sim-sur', 'SU-22', 'Mesa Jardín · sesión 3', 'SUR', 'mesa', '2026-11-16 06:00:00', 4, 0, false, '2026-11-12 14:57:52.37132');
INSERT INTO public.sim_items VALUES (24, 'sim-sur', 'SU-32', 'Mesa Barra · sesión 3', 'SUR', 'mesa', '2026-11-17 06:00:00', 4, 0, false, '2026-11-12 14:57:52.376437');
INSERT INTO public.sim_items VALUES (25, 'sim-este', 'ES-00', 'Kit Alfa · sesión 1', 'ESTE', 'unidad', '2026-11-14 00:00:00', 8, 0, false, '2026-11-12 14:57:52.378108');
INSERT INTO public.sim_items VALUES (28, 'sim-este', 'ES-10', 'Kit Beta · sesión 1', 'ESTE', 'unidad', '2026-11-15 00:00:00', 2, 0, false, '2026-11-12 14:57:52.38287');
INSERT INTO public.sim_items VALUES (29, 'sim-este', 'ES-11', 'Kit Beta · sesión 2', 'ESTE', 'unidad', '2026-11-15 03:00:00', 4, 0, false, '2026-11-12 14:57:52.384574');
INSERT INTO public.sim_items VALUES (32, 'sim-este', 'ES-21', 'Kit Gamma · sesión 2', 'ESTE', 'unidad', '2026-11-16 03:00:00', 6, 0, false, '2026-11-12 14:57:52.390049');
INSERT INTO public.sim_items VALUES (33, 'sim-este', 'ES-22', 'Kit Gamma · sesión 3', 'ESTE', 'unidad', '2026-11-16 06:00:00', 4, 0, false, '2026-11-12 14:57:52.391722');
INSERT INTO public.sim_items VALUES (34, 'sim-este', 'ES-30', 'Kit Delta · sesión 1', 'ESTE', 'unidad', '2026-11-17 00:00:00', 4, 0, false, '2026-11-12 14:57:52.393638');
INSERT INTO public.sim_items VALUES (36, 'sim-este', 'ES-32', 'Kit Delta · sesión 3', 'ESTE', 'unidad', '2026-11-17 06:00:00', 2, 0, false, '2026-11-12 14:57:52.397378');
INSERT INTO public.sim_items VALUES (40, 'sim-oeste', 'OE-10', 'Turno Consulta B · sesión 1', 'OESTE', 'turno', '2026-11-15 00:00:00', 6, 0, false, '2026-11-12 14:57:52.404489');
INSERT INTO public.sim_items VALUES (42, 'sim-oeste', 'OE-12', 'Turno Consulta B · sesión 3', 'OESTE', 'turno', '2026-11-15 06:00:00', 10, 0, false, '2026-11-12 14:57:52.407889');
INSERT INTO public.sim_items VALUES (44, 'sim-oeste', 'OE-21', 'Turno Taller · sesión 2', 'OESTE', 'turno', '2026-11-16 03:00:00', 2, 0, false, '2026-11-12 14:57:52.41245');
INSERT INTO public.sim_items VALUES (45, 'sim-oeste', 'OE-22', 'Turno Taller · sesión 3', 'OESTE', 'turno', '2026-11-16 06:00:00', 8, 0, false, '2026-11-12 14:57:52.414302');
INSERT INTO public.sim_items VALUES (47, 'sim-oeste', 'OE-31', 'Turno Asesoría · sesión 2', 'OESTE', 'turno', '2026-11-17 03:00:00', 2, 0, false, '2026-11-12 14:57:52.418024');
INSERT INTO public.sim_items VALUES (49, 'sim-centro', 'CE-00', 'Feria Nébula · sesión 1', 'CENTRO', 'entrada', '2026-11-14 00:00:00', 2, 0, false, '2026-11-12 14:57:52.421602');
INSERT INTO public.sim_items VALUES (51, 'sim-centro', 'CE-02', 'Feria Nébula · sesión 3', 'CENTRO', 'entrada', '2026-11-14 06:00:00', 6, 0, false, '2026-11-12 14:57:52.425396');
INSERT INTO public.sim_items VALUES (53, 'sim-centro', 'CE-11', 'Expo Prisma · sesión 2', 'CENTRO', 'entrada', '2026-11-15 03:00:00', 6, 0, false, '2026-11-12 14:57:52.43036');
INSERT INTO public.sim_items VALUES (56, 'sim-centro', 'CE-21', 'Foro Atlas · sesión 2', 'CENTRO', 'entrada', '2026-11-16 03:00:00', 2, 0, false, '2026-11-12 14:57:52.4355');
INSERT INTO public.sim_items VALUES (58, 'sim-centro', 'CE-30', 'Muestra Orión · sesión 1', 'CENTRO', 'entrada', '2026-11-17 00:00:00', 4, 0, false, '2026-11-12 14:57:52.440517');
INSERT INTO public.sim_items VALUES (59, 'sim-centro', 'CE-31', 'Muestra Orión · sesión 2', 'CENTRO', 'entrada', '2026-11-17 03:00:00', 6, 0, false, '2026-11-12 14:57:52.442671');
INSERT INTO public.sim_items VALUES (60, 'sim-centro', 'CE-32', 'Muestra Orión · sesión 3', 'CENTRO', 'entrada', '2026-11-17 06:00:00', 2, 0, false, '2026-11-12 14:57:52.444617');
INSERT INTO public.sim_items VALUES (61, 'api-sur', 'AS-0', 'Reserva API Ficticia · turno 1', 'SUR', 'mesa', NULL, 4, 0, false, '2026-11-12 14:57:52.467029');
INSERT INTO public.sim_items VALUES (63, 'api-sur', 'AS-2', 'Reserva API Ficticia · turno 3', 'SUR', 'mesa', NULL, 4, 0, false, '2026-11-12 14:57:52.472047');
INSERT INTO public.sim_items VALUES (22, 'sim-sur', 'SU-30', 'Mesa Barra · sesión 1', 'SUR', 'mesa', '2026-11-17 00:00:00', 10, 2, false, '2026-11-12 14:57:54.350856');
INSERT INTO public.sim_items VALUES (20, 'sim-sur', 'SU-21', 'Mesa Jardín · sesión 2', 'SUR', 'mesa', '2026-11-16 03:00:00', 10, 0, false, '2026-11-12 14:57:53.753071');
INSERT INTO public.sim_items VALUES (62, 'api-sur', 'AS-1', 'Reserva API Ficticia · turno 2', 'SUR', 'mesa', NULL, 4, 0, false, '2026-11-12 14:57:53.969057');
INSERT INTO public.sim_items VALUES (14, 'sim-sur', 'SU-01', 'Mesa Terraza · sesión 2', 'SUR', 'mesa', '2026-11-14 03:00:00', 2, 0, false, '2026-11-12 14:57:55.031172');
INSERT INTO public.sim_items VALUES (2, 'sim-norte', 'NO-01', 'Concierto Aurora · sesión 2', 'NORTE', 'entrada', '2026-11-14 06:00:00', 2, 0, false, '2026-11-12 14:57:54.380507');
INSERT INTO public.sim_items VALUES (7, 'sim-norte', 'NO-20', 'Gala Cometa · sesión 1', 'NORTE', 'entrada', '2026-11-16 00:00:00', 2, 0, false, '2026-11-12 14:57:55.020524');
INSERT INTO public.sim_items VALUES (13, 'sim-sur', 'SU-00', 'Mesa Terraza · sesión 1', 'SUR', 'mesa', '2026-11-14 00:00:00', 2, 0, false, '2026-11-12 14:57:52.620643');
INSERT INTO public.sim_items VALUES (8, 'sim-norte', 'NO-21', 'Gala Cometa · sesión 2', 'NORTE', 'entrada', '2026-11-16 03:00:00', 10, 0, true, '2026-11-12 14:57:54.382972');
INSERT INTO public.sim_items VALUES (50, 'sim-centro', 'CE-01', 'Feria Nébula · sesión 2', 'CENTRO', 'entrada', '2026-11-14 03:00:00', 10, 0, false, '2026-11-12 14:57:54.181736');
INSERT INTO public.sim_items VALUES (48, 'sim-oeste', 'OE-32', 'Turno Asesoría · sesión 3', 'OESTE', 'turno', '2026-11-17 06:00:00', 8, 0, false, '2026-11-12 14:57:53.756301');
INSERT INTO public.sim_items VALUES (46, 'sim-oeste', 'OE-30', 'Turno Asesoría · sesión 1', 'OESTE', 'turno', '2026-11-17 00:00:00', 10, 0, false, '2026-11-12 14:57:53.764103');
INSERT INTO public.sim_items VALUES (43, 'sim-oeste', 'OE-20', 'Turno Taller · sesión 1', 'OESTE', 'turno', '2026-11-16 00:00:00', 6, 3, false, '2026-11-12 14:57:53.768032');
INSERT INTO public.sim_items VALUES (31, 'sim-este', 'ES-20', 'Kit Gamma · sesión 1', 'ESTE', 'unidad', '2026-11-16 00:00:00', 6, 0, false, '2026-11-12 14:57:53.773833');
INSERT INTO public.sim_items VALUES (52, 'sim-centro', 'CE-10', 'Expo Prisma · sesión 1', 'CENTRO', 'entrada', '2026-11-15 00:00:00', 10, 0, false, '2026-11-12 14:57:53.973864');
INSERT INTO public.sim_items VALUES (37, 'sim-oeste', 'OE-00', 'Turno Consulta A · sesión 1', 'OESTE', 'turno', '2026-11-14 00:00:00', 2, 0, false, '2026-11-12 14:57:54.184681');
INSERT INTO public.sim_items VALUES (17, 'sim-sur', 'SU-11', 'Mesa Salón · sesión 2', 'SUR', 'mesa', '2026-11-15 03:00:00', 10, 4, false, '2026-11-12 14:57:55.026233');
INSERT INTO public.sim_items VALUES (39, 'sim-oeste', 'OE-02', 'Turno Consulta A · sesión 3', 'OESTE', 'turno', '2026-11-14 06:00:00', 2, 1, false, '2026-11-12 14:57:54.178017');
INSERT INTO public.sim_items VALUES (35, 'sim-este', 'ES-31', 'Kit Delta · sesión 2', 'ESTE', 'unidad', '2026-11-17 01:00:00', 6, 3, false, '2026-11-12 14:57:54.347779');
INSERT INTO public.sim_items VALUES (57, 'sim-centro', 'CE-22', 'Foro Atlas · sesión 3', 'CENTRO', 'entrada', '2026-11-16 06:00:00', 2, 0, true, '2026-11-12 14:57:54.353443');
INSERT INTO public.sim_items VALUES (41, 'sim-oeste', 'OE-11', 'Turno Consulta B · sesión 2', 'OESTE', 'turno', '2026-11-15 03:00:00', 6, 2, false, '2026-11-12 14:57:54.356604');
INSERT INTO public.sim_items VALUES (26, 'sim-este', 'ES-01', 'Kit Alfa · sesión 2', 'ESTE', 'unidad', '2026-11-14 03:00:00', 10, 1, false, '2026-11-12 14:57:54.35996');
INSERT INTO public.sim_items VALUES (54, 'sim-centro', 'CE-12', 'Expo Prisma · sesión 3', 'CENTRO', 'entrada', '2026-11-15 09:00:00', 10, 0, false, '2026-11-12 14:57:54.362673');
INSERT INTO public.sim_items VALUES (55, 'sim-centro', 'CE-20', 'Foro Atlas · sesión 1', 'CENTRO', 'entrada', '2026-11-16 00:00:00', 4, 0, false, '2026-11-12 14:57:54.365566');
INSERT INTO public.sim_items VALUES (27, 'sim-este', 'ES-02', 'Kit Alfa · sesión 3', 'ESTE', 'unidad', '2026-11-14 06:00:00', 6, 1, false, '2026-11-12 14:57:54.367792');
INSERT INTO public.sim_items VALUES (23, 'sim-sur', 'SU-31', 'Mesa Barra · sesión 2', 'SUR', 'mesa', '2026-11-17 03:00:00', 8, 2, true, '2026-11-12 14:57:55.035463');
INSERT INTO public.sim_items VALUES (30, 'sim-este', 'ES-12', 'Kit Beta · sesión 3', 'ESTE', 'unidad', '2026-11-15 06:00:00', 8, 1, false, '2026-11-12 14:57:54.374574');
INSERT INTO public.sim_items VALUES (15, 'sim-sur', 'SU-02', 'Mesa Terraza · sesión 3', 'SUR', 'mesa', '2026-11-14 06:00:00', 4, 1, false, '2026-11-12 14:57:55.014382');
INSERT INTO public.sim_items VALUES (38, 'sim-oeste', 'OE-01', 'Turno Consulta A · sesión 2', 'OESTE', 'turno', '2026-11-14 03:00:00', 8, 0, true, '2026-11-12 14:57:55.023636');


--
-- Data for Name: sim_runs; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: slot_states; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.slot_states VALUES (37, 4, 'OE-00', 'OESTE', 'turno', 'Turno Consulta A · sesión 1', '2026-11-14 00:00:00', 2, 0, 0, 'ACTIVO', '53a5f0a1a18c9cca89ad3e27888a4b51167133aeb619007f508cced6d007c841', 2, true, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (25, 3, 'ES-00', 'ESTE', 'unidad', 'Kit Alfa · sesión 1', '2026-11-14 00:00:00', 8, 0, 0, 'ACTIVO', '7ee6245616735f93697ceeb03ed65d42cecdb569efd90d2fdcececffc234d01d', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (16, 2, 'SU-10', 'SUR', 'mesa', 'Mesa Salón · sesión 1', '2026-11-15 00:00:00', 4, 0, 0, 'ACTIVO', 'dba13f548b10a1edb751c6e181177d600993a819951345b10a46a6a59fe3977e', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (18, 2, 'SU-12', 'SUR', 'mesa', 'Mesa Salón · sesión 3', '2026-11-15 06:00:00', 10, 0, 0, 'ACTIVO', '999b0af9dd36dc046f45f4281e84143426d1c79cc39af9ac928bf789bb5e6b30', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (19, 2, 'SU-20', 'SUR', 'mesa', 'Mesa Jardín · sesión 1', '2026-11-16 00:00:00', 2, 0, 0, 'ACTIVO', '7f9442bd968e9e911568589e06134cd8479c2b9bf7ead93aed375d7fe05ff49f', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (26, 3, 'ES-01', 'ESTE', 'unidad', 'Kit Alfa · sesión 2', '2026-11-14 03:00:00', 10, 1, 1, 'ACTIVO', '39cb3a883af4af300d0d2f97ced551727cdb221a1a8e677e3f92b2653a6c54d5', 2, true, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (27, 3, 'ES-02', 'ESTE', 'unidad', 'Kit Alfa · sesión 3', '2026-11-14 06:00:00', 6, 1, 0, 'ACTIVO', '6769999df9181d7032875cce031d9118e00233d246cc151ef2f8b73afc6d111f', 2, true, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (28, 3, 'ES-10', 'ESTE', 'unidad', 'Kit Beta · sesión 1', '2026-11-15 00:00:00', 2, 0, 0, 'ACTIVO', 'e59ba597d361fa190188ac6fe459a8497000d3aef500289b61805e5e948ea04f', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (29, 3, 'ES-11', 'ESTE', 'unidad', 'Kit Beta · sesión 2', '2026-11-15 03:00:00', 4, 0, 0, 'ACTIVO', '8c5e1dec63e8e2d140888d59c97ecf547efd2da6b307c13aee0fbe74ddc4f020', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (30, 3, 'ES-12', 'ESTE', 'unidad', 'Kit Beta · sesión 3', '2026-11-15 06:00:00', 8, 1, 0, 'ACTIVO', '8a304c670e1d2cfc5182e8318ddaebb730932f2412a2e5a19d6344332d995d88', 2, true, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (31, 3, 'ES-20', 'ESTE', 'unidad', 'Kit Gamma · sesión 1', '2026-11-16 00:00:00', 6, 0, 0, 'ACTIVO', '195c336db6a4713bb3d3d70365fdfc32f5b59dc9ee7b7b1bd7763f58bc64924c', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (32, 3, 'ES-21', 'ESTE', 'unidad', 'Kit Gamma · sesión 2', '2026-11-16 03:00:00', 6, 0, 0, 'ACTIVO', '425b4d070d9ecebf978db5c80aec39b6b2a9e7bfb509330960b57d6c2a6d80dd', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (20, 2, 'SU-21', 'SUR', 'mesa', 'Mesa Jardín · sesión 2', '2026-11-16 03:00:00', 10, 0, 0, 'ACTIVO', '32c81c7644810d1ce88347a7f1d36adb21a298aba90ed72c60bcbfba8d1d6f10', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (21, 2, 'SU-22', 'SUR', 'mesa', 'Mesa Jardín · sesión 3', '2026-11-16 06:00:00', 4, 0, 0, 'ACTIVO', '6c268f52b2ab916912a83fbb0cefc58059ba3a27f5b6fbc5091656c11de308bb', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (33, 3, 'ES-22', 'ESTE', 'unidad', 'Kit Gamma · sesión 3', '2026-11-16 06:00:00', 4, 0, 0, 'ACTIVO', '0aece1a05c210f58467fbda6dd8bda9f9d541833f984244c3d22997ea34535b9', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (22, 2, 'SU-30', 'SUR', 'mesa', 'Mesa Barra · sesión 1', '2026-11-17 00:00:00', 10, 2, 2, 'ACTIVO', '0ba1ef6a5853e42a9354271d3a6f3f483ea726810a439cecb47e270a9221468d', 2, true, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (38, 4, 'OE-01', 'OESTE', 'turno', 'Turno Consulta A · sesión 2', '2026-11-14 03:00:00', 8, 0, 0, 'CANCELADO', 'c4667c606e39fe65882364e8e28d78469afe2f0002cde2c3c2118f3df20025b9', 2, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (23, 2, 'SU-31', 'SUR', 'mesa', 'Mesa Barra · sesión 2', '2026-11-17 03:00:00', 8, 0, 0, 'CANCELADO', '36c07110dbf98f92ffaac38b72ce4694a95c2104f54d658a8ced6436c056c2b6', 3, true, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (15, 2, 'SU-02', 'SUR', 'mesa', 'Mesa Terraza · sesión 3', '2026-11-14 06:00:00', 4, 1, 1, 'ACTIVO', '15747aea3947d1ae8efae59aff770273a0008591b31751fee9ef14a84c7434c1', 2, true, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (34, 3, 'ES-30', 'ESTE', 'unidad', 'Kit Delta · sesión 1', '2026-11-17 00:00:00', 4, 0, 0, 'ACTIVO', 'd65a1cc9b15042c6e46b6b1bf9f4c776cd48622d39c402d9b6833886c412d322', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (35, 3, 'ES-31', 'ESTE', 'unidad', 'Kit Delta · sesión 2', '2026-11-17 01:00:00', 6, 3, 3, 'ACTIVO', '9c3f66124d8d10307005f33c3af40d024ed4acf975ad12840133f71c5b19bb61', 2, true, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (1, 1, 'NO-00', 'NORTE', 'entrada', 'Concierto Aurora · sesión 1', '2026-11-14 00:00:00', 2, 0, 0, 'ACTIVO', 'b93f616e1bb617e89e8862c67db9ab883b6324fac79517cf57717313e9321791', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (13, 2, 'SU-00', 'SUR', 'mesa', 'Mesa Terraza · sesión 1', '2026-11-14 00:00:00', 2, 0, 0, 'ACTIVO', '94e3dde85227a9cd9517b3d590b4dfcc2bce07df251fd2cc63b105458d4aeb15', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (14, 2, 'SU-01', 'SUR', 'mesa', 'Mesa Terraza · sesión 2', '2026-11-14 03:00:00', 2, 0, 0, 'ACTIVO', 'e61e2e4686ff8cf1ad4a6b6b1e6d8d44a431e0e0cc43ec7e43ccf1642d4a211a', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (24, 2, 'SU-32', 'SUR', 'mesa', 'Mesa Barra · sesión 3', '2026-11-17 06:00:00', 4, 0, 0, 'ACTIVO', 'f66d1e5feef73de46370d2dc7553f1d98d0a023d38a20453dbe038108a8804e8', 1, false, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (36, 3, 'ES-32', 'ESTE', 'unidad', 'Kit Delta · sesión 3', '2026-11-17 06:00:00', 2, 0, 0, 'ACTIVO', '7d910ef12712789b7464e8d012bb362ebf099e0a9bc70c3042745e89356913c3', 1, false, 0, '2026-11-12 14:57:52.728751', '2026-11-12 14:57:55.071031', '{}');
INSERT INTO public.slot_states VALUES (52, 5, 'CE-10', 'CENTRO', 'entrada', 'Expo Prisma · sesión 1', '2026-11-15 00:00:00', 10, 0, 0, 'ACTIVO', '4bff6c26aa5fcb2862f2631c88128e4790aab8482b62181ad09f8c4a3a4c9fea', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (17, 2, 'SU-11', 'SUR', 'mesa', 'Mesa Salón · sesión 2', '2026-11-15 03:00:00', 10, 4, 1, 'ACTIVO', 'b5861f621fdd06a9f4d7de1a055800a4fcbea7fa328ae70c6e9ae1e2153de1ed', 2, true, 0, '2026-11-12 14:57:52.684903', '2026-11-12 14:57:55.053605', '{}');
INSERT INTO public.slot_states VALUES (8, 1, 'NO-21', 'NORTE', 'entrada', 'Gala Cometa · sesión 2', '2026-11-16 03:00:00', 10, 0, 0, 'CANCELADO', '21a9aaadf2597d66be8e5afa7282275ba3d0f53760e122cf4c117f8c4e8eb54b', 2, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (39, 4, 'OE-02', 'OESTE', 'turno', 'Turno Consulta A · sesión 3', '2026-11-14 06:00:00', 2, 1, 1, 'ACTIVO', 'bed083da4f9cf46a59cff8062deeef6b4fe11140d452a519535858f6626129e3', 2, true, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (40, 4, 'OE-10', 'OESTE', 'turno', 'Turno Consulta B · sesión 1', '2026-11-15 00:00:00', 6, 0, 0, 'ACTIVO', 'e20cdc2a0fc4f172b6925a0a2485d572ce777718ce9e85ad821d469db276d5d9', 1, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (41, 4, 'OE-11', 'OESTE', 'turno', 'Turno Consulta B · sesión 2', '2026-11-15 03:00:00', 6, 2, 2, 'ACTIVO', '36808e29288b362e8d1556cfd9207949883bb09479a0d10e4a09799f7e8bf11b', 2, true, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (53, 5, 'CE-11', 'CENTRO', 'entrada', 'Expo Prisma · sesión 2', '2026-11-15 03:00:00', 6, 0, 0, 'ACTIVO', 'd2e0c7ef775e44de361249591af5fd8beb79ec3710fe2ce1f4afe48ac8479712', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (54, 5, 'CE-12', 'CENTRO', 'entrada', 'Expo Prisma · sesión 3', '2026-11-15 09:00:00', 10, 0, 0, 'ACTIVO', 'f2160111a75606e786c24a6ce64a822f4f1450ed3150ee6242b75bf51bde10d8', 2, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (9, 1, 'NO-22', 'NORTE', 'entrada', 'Gala Cometa · sesión 3', '2026-11-16 06:00:00', 2, 0, 0, 'ACTIVO', '8ece3eff39c1610d2d660e217e3d2b06abc997789abfd8129ca3be6afd0dd6df', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (55, 5, 'CE-20', 'CENTRO', 'entrada', 'Foro Atlas · sesión 1', '2026-11-16 00:00:00', 4, 0, 0, 'ACTIVO', '31912c8d5767ddabf63a2bdf8908a6f62aebf9cab5de6ecdbcf792fe07c5365f', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (42, 4, 'OE-12', 'OESTE', 'turno', 'Turno Consulta B · sesión 3', '2026-11-15 06:00:00', 10, 0, 0, 'ACTIVO', '771c8a92c5387feadeabe5daf2ae391c6af253dcbd2039416d7f752311acf316', 1, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (56, 5, 'CE-21', 'CENTRO', 'entrada', 'Foro Atlas · sesión 2', '2026-11-16 03:00:00', 2, 0, 0, 'ACTIVO', '9ea4f0025f9ccd2a5a0fd0c121deeb216d1a995306c0482dada9614ee95cf66a', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (49, 5, 'CE-00', 'CENTRO', 'entrada', 'Feria Nébula · sesión 1', '2026-11-14 00:00:00', 2, 0, 0, 'ACTIVO', '7df5e0ed76ea2615e7f8aa2489d0860acda709973b2ea8c38b141a94bf7d2113', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (2, 1, 'NO-01', 'NORTE', 'entrada', 'Concierto Aurora · sesión 2', '2026-11-14 06:00:00', 2, 0, 0, 'ACTIVO', '625e014a6fae50d97742d4b6d1b6791c8d50c23c253d747f2e0a3fa947c019cf', 2, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (3, 1, 'NO-02', 'NORTE', 'entrada', 'Concierto Aurora · sesión 3', '2026-11-14 06:00:00', 6, 0, 0, 'ACTIVO', '5b60b550bf44a7748a5bf9b443e1aacd0a7c29fde2443375f671de82e095b66e', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (4, 1, 'NO-10', 'NORTE', 'entrada', 'Festival Brisa · sesión 1', '2026-11-15 00:00:00', 4, 0, 0, 'ACTIVO', '1a20e355221639bdd5b25751f977d7459965fcb28487357604801a67ae67bb2f', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (5, 1, 'NO-11', 'NORTE', 'entrada', 'Festival Brisa · sesión 2', '2026-11-15 03:00:00', 4, 0, 0, 'ACTIVO', 'f91b58792970d73af9adbe73eb2d24ae3781bc9e79806c01ed70f0016a6333ee', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (6, 1, 'NO-12', 'NORTE', 'entrada', 'Festival Brisa · sesión 3', '2026-11-15 06:00:00', 4, 0, 0, 'ACTIVO', '8e68f6ec1851c67f30868992cf50cad73043864b5366dda0a9b15ef696b68cad', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (7, 1, 'NO-20', 'NORTE', 'entrada', 'Gala Cometa · sesión 1', '2026-11-16 00:00:00', 2, 0, 0, 'ACTIVO', '510ad0691f641e17a3c334fcbe588e1d807f6554912d9384b0a70ca9a8b1977e', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (10, 1, 'NO-30', 'NORTE', 'entrada', 'Teatro Lumen · sesión 1', '2026-11-17 00:00:00', 10, 0, 0, 'ACTIVO', 'ad4b3540e77d85063ad223e961d2c04a584fce7c48878d5ca9561391332b2cb0', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (11, 1, 'NO-31', 'NORTE', 'entrada', 'Teatro Lumen · sesión 2', '2026-11-17 03:00:00', 8, 0, 0, 'ACTIVO', '8a334572a0edcb0e6ae8b7c79e353d0a549faba4d933fcbdabccb1c434807183', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (50, 5, 'CE-01', 'CENTRO', 'entrada', 'Feria Nébula · sesión 2', '2026-11-14 03:00:00', 10, 0, 0, 'ACTIVO', 'd75caa994655e04c906de0cbecdae69c74ed8c5952c2f68357c6ced4a2005349', 3, true, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (51, 5, 'CE-02', 'CENTRO', 'entrada', 'Feria Nébula · sesión 3', '2026-11-14 06:00:00', 6, 0, 0, 'ACTIVO', '38d7a886209b24839ea675f5bfbb8c690166840535820bc8032527d71410c19b', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (57, 5, 'CE-22', 'CENTRO', 'entrada', 'Foro Atlas · sesión 3', '2026-11-16 06:00:00', 2, 0, 0, 'CANCELADO', 'f012fd98f71d05dc4fb9a0985ac7a1d718936e6bd81ff8524a9f64a60c471946', 2, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (58, 5, 'CE-30', 'CENTRO', 'entrada', 'Muestra Orión · sesión 1', '2026-11-17 00:00:00', 4, 0, 0, 'ACTIVO', 'a717c94c974b609aa66a02e344fd3f06fead670e03e4b1ee89a1d38be5e36dca', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (12, 1, 'NO-32', 'NORTE', 'entrada', 'Teatro Lumen · sesión 3', '2026-11-17 06:00:00', 2, 0, 0, 'ACTIVO', 'cfa1b9cad43e9e99cacf347d81f25e528a8b84c03c4c18473bb1fc82b90651ec', 1, false, 0, '2026-11-12 14:57:52.631692', '2026-11-12 14:57:55.041365', '{}');
INSERT INTO public.slot_states VALUES (44, 4, 'OE-21', 'OESTE', 'turno', 'Turno Taller · sesión 2', '2026-11-16 03:00:00', 2, 0, 0, 'ACTIVO', '376835b79b1aedd963c12775e083acea0ef15234ca66f867b4e34d6f7ce5bcae', 1, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (45, 4, 'OE-22', 'OESTE', 'turno', 'Turno Taller · sesión 3', '2026-11-16 06:00:00', 8, 0, 0, 'ACTIVO', '77c30184d7db947a723ab9dfa684e2e8cd67d5725f074f51ffec0d7d8abb0e8a', 1, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (46, 4, 'OE-30', 'OESTE', 'turno', 'Turno Asesoría · sesión 1', '2026-11-17 00:00:00', 10, 0, 0, 'ACTIVO', '61f39d208c4171fe09c9f4fa58e6660cd9502e5de735247f8edf4bc7b7ed78c5', 1, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (47, 4, 'OE-31', 'OESTE', 'turno', 'Turno Asesoría · sesión 2', '2026-11-17 03:00:00', 2, 0, 0, 'ACTIVO', '1657ce697e44fc691f2c344d3612aff0e99f2dc5c0bfa37f0878e15611839f48', 1, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (48, 4, 'OE-32', 'OESTE', 'turno', 'Turno Asesoría · sesión 3', '2026-11-17 06:00:00', 8, 0, 0, 'ACTIVO', '0d9a0adac9aa1c65e7c83a9655a156aa7302698b90ff47aabf37e1d3f8010772', 1, false, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');
INSERT INTO public.slot_states VALUES (59, 5, 'CE-31', 'CENTRO', 'entrada', 'Muestra Orión · sesión 2', '2026-11-17 03:00:00', 6, 0, 0, 'ACTIVO', '13800d87050ddc844aaa3b8298e41c8fc1a79fcf7e9eb0919d5171fc53015e53', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (60, 5, 'CE-32', 'CENTRO', 'entrada', 'Muestra Orión · sesión 3', '2026-11-17 06:00:00', 2, 0, 0, 'ACTIVO', '29dc3c53c1e282256d830b44e6c4e9d4ee86bb621e90fc9b7927ffd88b4be1ac', 1, false, 0, '2026-11-12 14:57:52.812951', '2026-11-12 14:57:55.087052', '{}');
INSERT INTO public.slot_states VALUES (43, 4, 'OE-20', 'OESTE', 'turno', 'Turno Taller · sesión 1', '2026-11-16 00:00:00', 6, 3, 3, 'ACTIVO', '24dd017f08f40245773e1bb403fc9c461d157e4d148e46fc673cb314226d4b74', 2, true, 0, '2026-11-12 14:57:52.773128', '2026-11-12 14:57:55.078943', '{}');


--
-- Data for Name: sources; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.sources VALUES (6, 'json-centro', 'Archivo JSON Centro', 'CENTRO', 'calendario', 'calendario', 'json', '{"path": "centro_calendario.json"}', 'ACTIVA', 10, 0.1, 600, 2, 'CERRADO', NULL, 0, 0, NULL, NULL, NULL, '{}', NULL, 0, 0, 0, 0, '2026-11-12 14:57:52.464077');
INSERT INTO public.sources VALUES (7, 'csv-norte', 'Archivo CSV Norte', 'NORTE', 'inventario', 'inventario', 'csv', '{"path": "norte_inventario.csv"}', 'ACTIVA', 10, 0.1, 600, 2, 'CERRADO', NULL, 0, 0, NULL, NULL, NULL, '{}', NULL, 0, 0, 0, 0, '2026-11-12 14:57:52.464083');
INSERT INTO public.sources VALUES (8, 'api-sur', 'API de prueba local Sur', 'SUR', 'reservacion', 'reservacion', 'http', '{"url": "http://127.0.0.1:8000/api-prueba/feeds/api-sur", "timeout_s": 3}', 'ACTIVA', 10, 0.1, 600, 2, 'CERRADO', NULL, 0, 0, NULL, NULL, NULL, '{}', NULL, 0, 0, 0, 0, '2026-11-12 14:57:52.464084');
INSERT INTO public.sources VALUES (1, 'sim-norte', 'Simulador Norte (evento)', 'NORTE', 'evento', 'evento', 'simulacion', '{"feed": "sim-norte", "error_rate": 0.02, "rate_limit_rate": 0.0}', 'ACTIVA', 5, 0.15, 600, 2, 'CERRADO', NULL, 0, 0, '2026-11-12 14:58:00.129672', '2026-11-12 14:57:55.041365', '2026-11-12 14:57:55.041365', '{"items": 12, "cambios": 0, "duplicados": 0, "sin_cambio": 12, "ms": 2.0}', NULL, 6, 0, 14, 1.853662000002032, '2026-11-12 14:57:52.319866');
INSERT INTO public.sources VALUES (2, 'sim-sur', 'Simulador Sur (reservacion)', 'SUR', 'reservacion', 'reservacion', 'simulacion', '{"feed": "sim-sur", "error_rate": 0.02, "rate_limit_rate": 0.0}', 'ACTIVA', 5, 0.15, 600, 2, 'CERRADO', NULL, 0, 0, '2026-11-12 14:57:59.352082', '2026-11-12 14:57:55.053605', '2026-11-12 14:57:55.053605', '{"items": 12, "cambios": 3, "duplicados": 0, "sin_cambio": 9, "ms": 1.61}', NULL, 6, 0, 16, 1.7777281666676952, '2026-11-12 14:57:52.319877');
INSERT INTO public.sources VALUES (3, 'sim-este', 'Simulador Este (inventario)', 'ESTE', 'inventario', 'inventario', 'simulacion', '{"feed": "sim-este", "error_rate": 0.02, "rate_limit_rate": 0.0}', 'ACTIVA', 5, 0.15, 600, 2, 'CERRADO', NULL, 0, 0, '2026-11-12 14:58:00.443053', '2026-11-12 14:57:55.071031', '2026-11-12 14:57:55.071031', '{"items": 12, "cambios": 0, "duplicados": 0, "sin_cambio": 12, "ms": 1.43}', NULL, 6, 0, 16, 1.5698268333323995, '2026-11-12 14:57:52.319879');
INSERT INTO public.sources VALUES (4, 'sim-oeste', 'Simulador Oeste (calendario)', 'OESTE', 'calendario', 'calendario', 'simulacion', '{"feed": "sim-oeste", "error_rate": 0.02, "rate_limit_rate": 0.0}', 'ACTIVA', 5, 0.15, 600, 2, 'CERRADO', NULL, 0, 0, '2026-11-12 14:58:00.663499', '2026-11-12 14:57:55.078943', '2026-11-12 14:57:55.078943', '{"items": 12, "cambios": 0, "duplicados": 0, "sin_cambio": 12, "ms": 0.96}', NULL, 6, 0, 17, 1.5914209999958, '2026-11-12 14:57:52.319882');
INSERT INTO public.sources VALUES (5, 'sim-centro', 'Simulador Centro (evento)', 'CENTRO', 'evento', 'evento', 'simulacion', '{"feed": "sim-centro", "error_rate": 0.02, "rate_limit_rate": 0.0}', 'ACTIVA', 5, 0.15, 600, 2, 'CERRADO', NULL, 0, 0, '2026-11-12 14:58:00.793498', '2026-11-12 14:57:55.087052', '2026-11-12 14:57:55.087052', '{"items": 12, "cambios": 0, "duplicados": 0, "sin_cambio": 12, "ms": 1.2}', NULL, 6, 0, 16, 1.5186558333321425, '2026-11-12 14:57:52.319884');


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.users VALUES (1, 'admin', 'scrypt$16384$8$1$GJwCJmrQBPUs4V/k/mFNjQ==$qBWaUXf99oung+IkMZKvt98Eoo8z2Ivx01GvWoLHh6U=', 'admin', true, '2026-11-12 14:57:52.545239');


--
-- Name: alerts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.alerts_id_seq', 28, true);


--
-- Name: api_tokens_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.api_tokens_id_seq', 1, false);


--
-- Name: assignments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.assignments_id_seq', 17, true);


--
-- Name: audit_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.audit_log_id_seq', 1, false);


--
-- Name: change_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.change_events_id_seq', 79, true);


--
-- Name: channels_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.channels_id_seq', 6, true);


--
-- Name: deliveries_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.deliveries_id_seq', 84, true);


--
-- Name: notification_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.notification_log_id_seq', 84, true);


--
-- Name: operators_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.operators_id_seq', 6, true);


--
-- Name: poll_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.poll_log_id_seq', 30, true);


--
-- Name: request_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.request_events_id_seq', 62, true);


--
-- Name: requests_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.requests_id_seq', 40, true);


--
-- Name: sim_items_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.sim_items_id_seq', 63, true);


--
-- Name: sim_runs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.sim_runs_id_seq', 1, false);


--
-- Name: slot_states_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.slot_states_id_seq', 60, true);


--
-- Name: sources_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.sources_id_seq', 8, true);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.users_id_seq', 1, true);


--
-- Name: alerts alerts_dedup_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_dedup_key_key UNIQUE (dedup_key);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: api_tokens api_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_pkey PRIMARY KEY (id);


--
-- Name: api_tokens api_tokens_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: assignments assignments_live_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_live_request_id_key UNIQUE (live_request_id);


--
-- Name: assignments assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: change_events change_events_event_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_events
    ADD CONSTRAINT change_events_event_key_key UNIQUE (event_key);


--
-- Name: change_events change_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_events
    ADD CONSTRAINT change_events_pkey PRIMARY KEY (id);


--
-- Name: channels channels_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT channels_name_key UNIQUE (name);


--
-- Name: channels channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT channels_pkey PRIMARY KEY (id);


--
-- Name: deliveries deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliveries
    ADD CONSTRAINT deliveries_pkey PRIMARY KEY (id);


--
-- Name: notification_log notification_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_log
    ADD CONSTRAINT notification_log_pkey PRIMARY KEY (id);


--
-- Name: operators operators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT operators_pkey PRIMARY KEY (id);


--
-- Name: poll_log poll_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.poll_log
    ADD CONSTRAINT poll_log_pkey PRIMARY KEY (id);


--
-- Name: regions regions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regions
    ADD CONSTRAINT regions_pkey PRIMARY KEY (code);


--
-- Name: request_events request_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_events
    ADD CONSTRAINT request_events_pkey PRIMARY KEY (id);


--
-- Name: requests requests_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_code_key UNIQUE (code);


--
-- Name: requests requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: sim_items sim_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_items
    ADD CONSTRAINT sim_items_pkey PRIMARY KEY (id);


--
-- Name: sim_runs sim_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_runs
    ADD CONSTRAINT sim_runs_pkey PRIMARY KEY (id);


--
-- Name: slot_states slot_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.slot_states
    ADD CONSTRAINT slot_states_pkey PRIMARY KEY (id);


--
-- Name: sources sources_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_code_key UNIQUE (code);


--
-- Name: sources sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_pkey PRIMARY KEY (id);


--
-- Name: deliveries uq_delivery_alert_channel; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliveries
    ADD CONSTRAINT uq_delivery_alert_channel UNIQUE (alert_id, channel_id);


--
-- Name: sim_items uq_sim_feed_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_items
    ADD CONSTRAINT uq_sim_feed_key UNIQUE (feed, resource_key);


--
-- Name: slot_states uq_slot_source_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.slot_states
    ADD CONSTRAINT uq_slot_source_key UNIQUE (source_id, resource_key);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: ix_alerts_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alerts_created_at ON public.alerts USING btree (created_at);


--
-- Name: ix_alerts_region; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alerts_region ON public.alerts USING btree (region);


--
-- Name: ix_alerts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alerts_status ON public.alerts USING btree (status);


--
-- Name: ix_assignments_operator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_operator_id ON public.assignments USING btree (operator_id);


--
-- Name: ix_assignments_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_request_id ON public.assignments USING btree (request_id);


--
-- Name: ix_assignments_slot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_slot_id ON public.assignments USING btree (slot_id);


--
-- Name: ix_assignments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignments_status ON public.assignments USING btree (status);


--
-- Name: ix_audit_log_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_ts ON public.audit_log USING btree (ts);


--
-- Name: ix_change_events_change_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_change_events_change_type ON public.change_events USING btree (change_type);


--
-- Name: ix_change_events_detected_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_change_events_detected_at ON public.change_events USING btree (detected_at);


--
-- Name: ix_change_events_processed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_change_events_processed ON public.change_events USING btree (processed);


--
-- Name: ix_change_events_region; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_change_events_region ON public.change_events USING btree (region);


--
-- Name: ix_change_events_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_change_events_source_id ON public.change_events USING btree (source_id);


--
-- Name: ix_deliveries_alert_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deliveries_alert_id ON public.deliveries USING btree (alert_id);


--
-- Name: ix_deliveries_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deliveries_status ON public.deliveries USING btree (status);


--
-- Name: ix_notification_log_alert_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_log_alert_id ON public.notification_log USING btree (alert_id);


--
-- Name: ix_notification_log_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_log_ts ON public.notification_log USING btree (ts);


--
-- Name: ix_poll_log_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_poll_log_source_id ON public.poll_log USING btree (source_id);


--
-- Name: ix_poll_log_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_poll_log_started_at ON public.poll_log USING btree (started_at);


--
-- Name: ix_request_events_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_request_events_request_id ON public.request_events USING btree (request_id);


--
-- Name: ix_requests_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_requests_created_at ON public.requests USING btree (created_at);


--
-- Name: ix_requests_matching; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_requests_matching ON public.requests USING btree (status, region, priority, created_at);


--
-- Name: ix_requests_region; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_requests_region ON public.requests USING btree (region);


--
-- Name: ix_requests_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_requests_status ON public.requests USING btree (status);


--
-- Name: ix_sim_items_feed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sim_items_feed ON public.sim_items USING btree (feed);


--
-- Name: ix_slot_states_region; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_slot_states_region ON public.slot_states USING btree (region);


--
-- Name: ix_slot_states_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_slot_states_source_id ON public.slot_states USING btree (source_id);


--
-- Name: alerts alerts_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES public.assignments(id);


--
-- Name: alerts alerts_change_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_change_event_id_fkey FOREIGN KEY (change_event_id) REFERENCES public.change_events(id);


--
-- Name: alerts alerts_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.requests(id);


--
-- Name: api_tokens api_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: assignments assignments_change_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_change_event_id_fkey FOREIGN KEY (change_event_id) REFERENCES public.change_events(id);


--
-- Name: assignments assignments_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operators(id);


--
-- Name: assignments assignments_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.requests(id);


--
-- Name: assignments assignments_slot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_slot_id_fkey FOREIGN KEY (slot_id) REFERENCES public.slot_states(id);


--
-- Name: change_events change_events_slot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_events
    ADD CONSTRAINT change_events_slot_id_fkey FOREIGN KEY (slot_id) REFERENCES public.slot_states(id);


--
-- Name: change_events change_events_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.change_events
    ADD CONSTRAINT change_events_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id);


--
-- Name: deliveries deliveries_alert_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliveries
    ADD CONSTRAINT deliveries_alert_id_fkey FOREIGN KEY (alert_id) REFERENCES public.alerts(id);


--
-- Name: deliveries deliveries_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deliveries
    ADD CONSTRAINT deliveries_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id);


--
-- Name: operators operators_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT operators_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: poll_log poll_log_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.poll_log
    ADD CONSTRAINT poll_log_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id);


--
-- Name: request_events request_events_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_events
    ADD CONSTRAINT request_events_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.requests(id);


--
-- Name: requests requests_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operators(id);


--
-- Name: requests requests_region_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_region_fkey FOREIGN KEY (region) REFERENCES public.regions(code);


--
-- Name: slot_states slot_states_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.slot_states
    ADD CONSTRAINT slot_states_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id);


--
-- Name: sources sources_region_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_region_fkey FOREIGN KEY (region) REFERENCES public.regions(code);


--
-- PostgreSQL database dump complete
--

\unrestrict jii8ffNFhaOTDvJyTFF0oRunTm91l3gmXhris0hMx6nbnotAdr2rQAwl6PuZaM0

