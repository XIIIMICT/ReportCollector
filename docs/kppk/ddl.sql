-- raw.kppk_d_org definition

-- Drop table

-- DROP TABLE raw.kppk_d_org;

CREATE TABLE raw.kppk_d_org (
	id int4 NOT NULL,
	code varchar(20) NOT NULL,
	"name" varchar(200) NOT NULL,
	time_zone int2 NOT NULL
);


-- raw.kppk_d_period definition

-- Drop table

-- DROP TABLE raw.kppk_d_period;

CREATE TABLE raw.kppk_d_period (
	id int4 NOT NULL,
	month_name varchar(20) NOT NULL,
	year_num int4 NOT NULL,
	first_date date NOT NULL,
	last_date date NOT NULL
);


-- raw.kppk_d_product_seq definition

-- Drop table

-- DROP TABLE raw.kppk_d_product_seq;

CREATE TABLE raw.kppk_d_product_seq (
	num int4 NOT NULL,
	product varchar(255) NOT NULL
);


-- raw.kppk_d_raw_seq definition

-- Drop table

-- DROP TABLE raw.kppk_d_raw_seq;

CREATE TABLE raw.kppk_d_raw_seq (
	num int4 NOT NULL,
	raw varchar(2000) NOT NULL
);


-- raw.kppk_f_realization definition

-- Drop table

-- DROP TABLE raw.kppk_f_realization;

CREATE TABLE raw.kppk_f_realization (
	org_id int4 NOT NULL,
	rep_date date NOT NULL,
	load_id int4 NULL,
	period_id int4 NOT NULL,
	product varchar(200) NOT NULL,
	region varchar(10) NOT NULL,
	month_plan numeric(20, 3) NULL,
	month_part_plan numeric(20, 3) NULL,
	fca_price_plan numeric(20, 2) NULL,
	var_exp_plan numeric(20, 2) NULL,
	export_duty_proc numeric(10, 3) NULL,
	fact numeric(20, 3) NULL,
	fca_price_fact numeric(20, 2) NULL,
	var_exp_fact numeric(20, 2) NULL,
	stock numeric(20, 3) NULL,
	plan_to_sell numeric(20, 3) NULL,
	export_duty_fact numeric(20, 3) NULL,
	export_duty_plan numeric(20, 3) NULL,
	excise_plan numeric(20, 2) NULL,
	excise_fact numeric(20, 2) NULL,
	full_cost_plan numeric(20, 2) NULL,
	full_cost_fact numeric(20, 2) NULL,
	input_cost_plan numeric(20, 2) NULL,
	input_cost_fact numeric(20, 2) NULL,
	stock_current numeric(20, 3) NULL,
	fca_price_market_plan numeric(20, 2) NULL,
	fca_price_market_fact numeric(20, 2) NULL,
	lastdate timestamp NULL
);


-- raw.kppk_f_realization_plan definition

-- Drop table

-- DROP TABLE raw.kppk_f_realization_plan;

CREATE TABLE raw.kppk_f_realization_plan (
	org_id int4 NOT NULL,
	rep_date date NOT NULL,
	load_id int4 NULL,
	period_id int4 NOT NULL,
	product varchar(255) NOT NULL,
	region varchar(10) NOT NULL,
	month_bp_plan numeric(20, 3) NULL,
	month_max_plan numeric(20, 3) NULL,
	month_opt_plan numeric(20, 3) NULL,
	fca_price_bp_plan numeric(20, 2) NULL,
	fca_price_max_plan numeric(20, 2) NULL,
	fca_price_opt_plan numeric(20, 2) NULL,
	var_exp_bp_plan numeric(20, 2) NULL,
	var_exp_plan numeric(20, 2) NULL,
	export_duty_bp numeric(20, 2) NULL,
	export_duty_max_plan numeric(20, 2) NULL,
	export_duty_opt_plan numeric(20, 2) NULL,
	excise_bp numeric(20, 2) NULL,
	excise_max_plan numeric(20, 2) NULL,
	excise_opt_plan numeric(20, 2) NULL,
	input_cost_bp numeric(20, 2) NULL,
	input_cost_plan numeric(20, 2) NULL,
	fca_price_market_bp_plan numeric(20, 2) NULL,
	fca_price_market_max_plan numeric(20, 2) NULL,
	fca_price_market_opt_plan numeric(20, 2) NULL,
	lastdate timestamp NULL
);

-- dict.markets definition

-- Drop table

-- DROP TABLE dict.markets;

CREATE TABLE dict.markets (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	code text NOT NULL,
	"name" text NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz NULL,
	deleted_at timestamptz NULL,
	CONSTRAINT markets_name_key UNIQUE (name),
	CONSTRAINT markets_pkey PRIMARY KEY (id)
);
