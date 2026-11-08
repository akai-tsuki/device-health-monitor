-- ユーザー一覧（マスタ）とデバイスデータ登録テーブルを作成する。
-- 詳細は docs/design.md の「4. データベース設計」を参照。
-- 旧リポジトリ（data-update-monitor）の 0001 / 0002 を1本にまとめ直したもの（旧データは保全不要）。

BEGIN;

CREATE TABLE users (
    user_id    TEXT PRIMARY KEY,
    group_id   TEXT NOT NULL,
    user_type  TEXT NOT NULL DEFAULT 'default',  -- ユーザをグルーピングするタイプのコード
    status     TEXT NOT NULL DEFAULT 'active'    -- 'active' or 'disabled'
);

CREATE INDEX idx_users_group  ON users (group_id);
CREATE INDEX idx_users_status ON users (status);

CREATE TABLE input_records (
    id               BIGSERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL,
    processed_at     TIMESTAMPTZ NOT NULL,   -- デバイス側でデータを処理・送信した時刻
    registered_at    TIMESTAMPTZ NOT NULL,   -- DBに登録された時刻
    battery_voltage  NUMERIC(4,2)            -- デバイスのバッテリー電圧（V）。NULL許容
);

CREATE INDEX idx_input_records_user_registered
    ON input_records (user_id, registered_at DESC);
CREATE INDEX idx_input_records_registered
    ON input_records (registered_at);

COMMIT;
