# GCPインフラ構成図

`terraform/` が構築する Google Cloud / Grafana Cloud のリソースと、その間を流れるデータ・制御・
認証情報のフロー。旧リポジトリ（data-update-monitor）の構成から Cloud Monitoring の経路
（カスタムメトリクス書込・`googlecloudmonitoring` receiver・Mimir）を削除し、デバイスシミュレーターを
GCE 上に追加している。

```mermaid
flowchart TB
    subgraph PROJECT["GCPプロジェクト (project_id)"]
        SCHED["Cloud Scheduler<br/>check-trigger-*<br/>*/5 * * * *"]
        RUN["Cloud Run Job<br/>check-job-*<br/>4種類のチェック"]
        LOG["Cloud Logging<br/>check_result / summary / detail"]
        SINK["Log Router sink<br/>check-job-logs-sink-*"]
        PUBSUB["Pub/Sub<br/>topic + subscription<br/>check-job-logs-*"]
        AR["Artifact Registry<br/>check-job-*"]
        SM_DB["Secret Manager<br/>db-password-*"]
        SM_GF["Secret Manager<br/>grafana-cloud-api-key-*"]

        subgraph VPC["VPC: vpc-* / subnet 10.10.0.0/24 (PSA)"]
            SQL["Cloud SQL for PostgreSQL<br/>pg-*<br/>users / input_records"]
            subgraph GCE["GCE: otel-collector-* （外部IPなし）"]
                OTEL["Otel Collector<br/>logs パイプライン"]
                SIM["device-simulator.service<br/>10秒ごとにINSERT"]
            end
        end
    end

    subgraph EXTERNAL["外部"]
        GRAFANA["Grafana Cloud<br/>Loki / Dashboard / Alert"]
        OPS["運用者"]
    end

    SCHED -->|"実行トリガー (scheduler-sa)"| RUN
    AR -.->|"イメージ pull"| RUN
    RUN -->|"クエリ (プライベートIP直結)"| SQL
    RUN -.->|"シークレット取得 (check-job-sa)"| SM_DB
    RUN -->|"標準出力 JSON"| LOG
    LOG -->|"sinkでルーティング"| SINK
    SINK -->|"publish"| PUBSUB
    OTEL -->|"購読 (pubsub.subscriber)"| PUBSUB
    OTEL -.->|"シークレット取得 (otel-collector-sa)"| SM_GF
    OTEL -->|"otlp_http (Cloud NAT経由)"| GRAFANA
    SIM -->|"INSERT"| SQL
    SIM -.->|"シークレット取得 (otel-collector-sa)"| SM_DB
    GRAFANA -->|"Alert (該当>0 / 失敗 / 途絶)"| OPS
    OPS -.->|"Dashboard・詳細ログで user_id 確認"| GRAFANA

    style PROJECT fill:#fafbfa,stroke:#c7d0d3,stroke-width:1px
    style VPC fill:#f4f8f7,stroke:#5b7a80,stroke-width:1.5px,stroke-dasharray: 4 3
    style GCE fill:#eef5f4,stroke:#1f6f6b,stroke-width:1px
    style EXTERNAL fill:#fff8ef,stroke:#e0ad73,stroke-width:1px,stroke-dasharray: 2 2

    classDef compute fill:#dceeec,stroke:#1f6f6b,stroke-width:1.5px,color:#122023
    classDef data fill:#e2e9f2,stroke:#3d5a80,stroke-width:1.5px,color:#122023
    classDef security fill:#f1e3ce,stroke:#8a5a2b,stroke-width:1.5px,color:#3a2a15
    classDef support fill:#eaecea,stroke:#5b7a80,stroke-width:1.5px,color:#122023
    classDef external fill:#fbe6c8,stroke:#c9762b,stroke-width:1.5px,color:#3a2a15

    class SCHED,RUN,OTEL,SIM compute
    class SQL,LOG data
    class SM_DB,SM_GF security
    class AR,SINK,PUBSUB support
    class GRAFANA,OPS external
```

## 凡例

| 種別 | 色 | 対象 |
|---|---|---|
| コンピュート | 緑 | Cloud Scheduler / Cloud Run Job / Otel Collector / デバイスシミュレーター |
| データストア | 青 | Cloud SQL、Cloud Logging |
| シークレット | 茶 | Secret Manager（DBパスワード／Grafana OTLP APIキー） |
| サポートリソース | 灰 | Artifact Registry、Log Router sink、Pub/Sub |
| 外部 | 橙 | Grafana Cloud、運用者 |

- **実線矢印**：データ・制御フロー
- **点線矢印**：IAM経由のアクセス（シークレット取得・イメージpull）／運用者の確認
- **破線の枠（VPC）**：プライベートIP範囲の境界。外部IPを持たない

デバイスシミュレーターは検証・デモ専用（`enable_device_simulator` で有無を切り替え）。実際のシステムでは
「デバイス → 受信側 → DB登録」の経路になるが、監視対象は DB に登録されたデータのため検証では直接 INSERT する。
