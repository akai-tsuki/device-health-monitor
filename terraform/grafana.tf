# Grafana Cloud側の Dashboard / Alert rule。docs/design.md 8章、docs/log-spec.md、
# docs/grafana-implementation-notes.md を参照。
#
# データソースは Loki のみ（旧リポジトリの Mimir / Cloud Monitoring メトリクスは使わない）。
# 数値はすべてチェックアプリのログ（summary / check_result）から LogQL で集計する。
#
# design.md 8.1節の確認結果（2026-11-07 実機）：jsonPayload は Loki 上でログ本文（JSON文字列）になり、
# `| json` でフィールドを取り出せる。ストリームは Otel Collector が付与する service_name="check-job-<env>"
# で選択する（docs/grafana-implementation-notes.md 5章）。
#
# `| json` を引数なしで使うと record_id 等の一意な値までラベルになり、ログ1行ごとに別系列になる。
# 数値クエリでは必要なフィールドだけを抽出し（`| json a="a", ...`）、外側で by (...) 集約する。

locals {
  loki_sel = (var.grafana_loki_stream_selector != "" ? var.grafana_loki_stream_selector
  : "{service_name=\"check-job-${var.environment}\"}")

  # anomaly_only = true のチェック種類は、該当1人以上のグループだけ summary が出る（log-spec.md 6章）。
  # グループ別パネルは正常なグループが表示されず、全グループ正常なら no_value の文言を出す。
  check_types = [
    { key = "missing", title = "データ欠損", alert = "人データ欠損しています", anomaly_only = false },
    { key = "delay", title = "データ遅延", alert = "人データ遅延が発生しています", anomaly_only = true },
    { key = "lost", title = "一時ロスト", alert = "人一時ロストが発生しています", anomaly_only = false },
    { key = "low_battery", title = "バッテリー低下", alert = "人バッテリーが低下しています", anomaly_only = true },
  ]

  # グループ別の該当ユーザー数（summary ログ、ユーザタイプは合算）
  q_group_anomaly = {
    for ct in local.check_types : ct.key => join(" ", [
      "sum by (group_id) (max by (group_id, user_type) (max_over_time(",
      "${local.loki_sel} |= \"summary\"",
      "| json event_type=\"event_type\", check_type=\"check_type\", group_id=\"group_id\", user_type=\"user_type\", anomaly_user_count=\"anomaly_user_count\"",
      "| event_type=\"summary\" | check_type=\"${ct.key}\"",
      "| unwrap anomaly_user_count [6m])))",
    ])
  }

  # 全グループ合計（成功した check_result ログ）。field は anomaly_user_count / monitored_user_count
  q_total = {
    for pair in setproduct([for ct in local.check_types : ct.key], ["anomaly_user_count", "monitored_user_count"]) :
    "${pair[0]}/${pair[1]}" => join(" ", [
      "max(max_over_time(",
      "${local.loki_sel} |= \"check_result\"",
      "| json event_type=\"event_type\", check_type=\"check_type\", success=\"success\", ${pair[1]}=\"${pair[1]}\"",
      "| event_type=\"check_result\" | check_type=\"${pair[0]}\" | success=\"true\"",
      "| unwrap ${pair[1]} [6m]))",
    ])
  }

  q_detail_logs = {
    for ct in local.check_types :
    ct.key => "${local.loki_sel} |= \"detail\" | json | event_type=\"detail\" | check_type=\"${ct.key}\""
  }

  q_check_success_count = join(" ", [
    "sum by (check_type) (count_over_time(",
    "${local.loki_sel} |= \"check_result\"",
    "| json event_type=\"event_type\", check_type=\"check_type\", success=\"success\"",
    "| event_type=\"check_result\" | success=\"true\" [6m]))",
  ])

  q_check_duration = join(" ", [
    "max by (check_type) (max_over_time(",
    "${local.loki_sel} |= \"check_result\"",
    "| json event_type=\"event_type\", check_type=\"check_type\", duration_ms=\"duration_ms\"",
    "| event_type=\"check_result\" | unwrap duration_ms [6m]))",
  ])

  q_check_failed_logs = "${local.loki_sel} |= \"check_result\" | json | event_type=\"check_result\" | success=\"false\""

  q_check_failed_count = join(" ", [
    "sum by (check_type, error_code) (count_over_time(",
    "${local.loki_sel} |= \"check_result\"",
    "| json event_type=\"event_type\", check_type=\"check_type\", success=\"success\", error_code=\"error_code\"",
    "| event_type=\"check_result\" | success=\"false\" [6m]))",
  ])

  q_check_result_count_15m = join(" ", [
    "sum(count_over_time(",
    "${local.loki_sel} |= \"check_result\"",
    "| json event_type=\"event_type\" | event_type=\"check_result\" [15m]))",
  ])

  loki_ds = {
    type = "loki"
    uid  = data.grafana_data_source.loki.uid
  }
}

data "grafana_data_source" "loki" {
  name = var.grafana_loki_datasource_name
}

resource "grafana_folder" "device_health" {
  title = "デバイスデータ監視 (${var.environment})"
}

# 通知先メールが未設定（空文字）の場合は作成しない
resource "grafana_contact_point" "email" {
  count = var.alert_notification_email != "" ? 1 : 0

  name = "device-health-monitor-email-${var.environment}"

  email {
    addresses = [var.alert_notification_email]
    subject   = "[デバイスデータ監視] {{ .CommonLabels.alertname }} ({{ len .Alerts.Firing }}件)"
  }
}

# ============================================================
# Dashboard（design.md 8.2節）
#   チェック種類ごとに1行（row）：該当数 / 監視数 / グループ別Table / 推移 / 詳細ログ
#   稼働状況行：チェック種類ごとの成功、duration_ms の推移、失敗ログ一覧
# Stat / Table は Instant クエリが機能しなかったため（grafana-implementation-notes.md）、
# Range クエリ＋最新値（Stat: lastNotNull、Table: reduce seriesToRows）で表示する。
# ============================================================
locals {
  row_height = 17

  check_panels = flatten([
    for i, ct in local.check_types : [
      {
        id        = i * 10 + 1
        type      = "row"
        title     = ct.title
        collapsed = false
        gridPos   = { h = 1, w = 24, x = 0, y = i * local.row_height }
        panels    = []
      },
      {
        id         = i * 10 + 2
        type       = "stat"
        title      = "${ct.title} 該当ユーザー数（全グループ）"
        gridPos    = { h = 8, w = 4, x = 0, y = i * local.row_height + 1 }
        datasource = local.loki_ds
        targets    = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_total["${ct.key}/anomaly_user_count"] }]
        options = {
          reduceOptions = { calcs = ["lastNotNull"], fields = "", values = false }
          colorMode     = "background"
        }
        fieldConfig = {
          defaults = {
            thresholds = {
              mode  = "absolute"
              steps = [{ color = "green", value = null }, { color = "red", value = 1 }]
            }
          }
          overrides = []
        }
      },
      {
        id         = i * 10 + 3
        type       = "stat"
        title      = "監視ユーザー数（全グループ）"
        gridPos    = { h = 8, w = 4, x = 4, y = i * local.row_height + 1 }
        datasource = local.loki_ds
        targets    = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_total["${ct.key}/monitored_user_count"] }]
        options = {
          reduceOptions = { calcs = ["lastNotNull"], fields = "", values = false }
        }
      },
      {
        id          = i * 10 + 4
        type        = "table"
        title       = "グループ別 ${ct.title} 該当ユーザー数（${ct.anomaly_only ? "該当ありのグループのみ、" : ""}多い順）"
        gridPos     = { h = 8, w = 16, x = 8, y = i * local.row_height + 1 }
        datasource  = local.loki_ds
        targets     = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_group_anomaly[ct.key], legendFormat = "{{group_id}}" }]
        fieldConfig = { defaults = { noValue = ct.anomaly_only ? "該当なし" : "No data" }, overrides = [] }
        transformations = [
          { id = "reduce", options = { reducers = ["lastNotNull"], mode = "seriesToRows" } },
          { id = "organize", options = { renameByName = { "Field" = "グループID", "Last *" = "該当ユーザー数" } } },
          { id = "sortBy", options = { fields = {}, sort = [{ field = "該当ユーザー数", desc = true }] } },
        ]
      },
      {
        id          = i * 10 + 5
        type        = "timeseries"
        title       = "${ct.title} 該当ユーザー数の推移（グループ別${ct.anomaly_only ? "、該当ありの期間のみ" : ""}）"
        gridPos     = { h = 8, w = 12, x = 0, y = i * local.row_height + 9 }
        datasource  = local.loki_ds
        targets     = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_group_anomaly[ct.key], legendFormat = "{{group_id}}" }]
        fieldConfig = { defaults = { noValue = ct.anomaly_only ? "該当なし" : "No data" }, overrides = [] }
      },
      {
        id         = i * 10 + 6
        type       = "logs"
        title      = "${ct.title} 詳細ログ"
        gridPos    = { h = 8, w = 12, x = 12, y = i * local.row_height + 9 }
        datasource = local.loki_ds
        targets    = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_detail_logs[ct.key] }]
      },
    ]
  ])

  status_y = length(local.check_types) * local.row_height

  status_panels = [
    {
      id        = 101
      type      = "row"
      title     = "チェック稼働状況"
      collapsed = false
      gridPos   = { h = 1, w = 24, x = 0, y = local.status_y }
      panels    = []
    },
    {
      id         = 102
      type       = "stat"
      title      = "直近6分の成功回数（チェック種類別。0なら失敗または途絶）"
      gridPos    = { h = 8, w = 8, x = 0, y = local.status_y + 1 }
      datasource = local.loki_ds
      targets    = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_check_success_count, legendFormat = "{{check_type}}" }]
      options = {
        reduceOptions = { calcs = ["lastNotNull"], fields = "", values = false }
        colorMode     = "background"
      }
      fieldConfig = {
        defaults = {
          noValue = "0"
          thresholds = {
            mode  = "absolute"
            steps = [{ color = "red", value = null }, { color = "green", value = 1 }]
          }
        }
        overrides = []
      }
    },
    {
      id         = 103
      type       = "timeseries"
      title      = "処理時間 duration_ms（チェック種類別）"
      gridPos    = { h = 8, w = 16, x = 8, y = local.status_y + 1 }
      datasource = local.loki_ds
      targets    = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_check_duration, legendFormat = "{{check_type}}" }]
      fieldConfig = {
        defaults  = { unit = "ms" }
        overrides = []
      }
    },
    {
      id         = 104
      type       = "logs"
      title      = "チェック失敗ログ"
      gridPos    = { h = 8, w = 24, x = 0, y = local.status_y + 9 }
      datasource = local.loki_ds
      targets    = [{ refId = "A", datasource = local.loki_ds, queryType = "range", expr = local.q_check_failed_logs }]
    },
  ]
}

resource "grafana_dashboard" "device_health" {
  folder = grafana_folder.device_health.uid

  config_json = jsonencode({
    title         = "デバイスデータ監視 (${var.environment})"
    uid           = "device-health-monitor-${var.environment}"
    timezone      = var.system_timezone
    schemaVersion = 39
    refresh       = "1m"
    time = {
      from = "now-6h"
      to   = "now"
    }
    panels = concat(local.check_panels, local.status_panels)
  })
}

# ============================================================
# Alert rule（design.md 8.3節）
# reduce → threshold のパイプラインで group_id 等のラベルを保持したまま評価する
# （classic_conditions はラベルを破棄するため使わない）。
# ============================================================
locals {
  alert_rules = concat(
    [
      for ct in local.check_types : {
        name           = ct.title
        expr           = local.q_group_anomaly[ct.key]
        lookback       = 600
        evaluator      = { type = "gt", params = [0] }
        no_data_state  = "OK"
        summary        = "グループ {{ $labels.group_id }} で {{ $values.B.Value }}${ct.alert}"
        severity_label = "warning"
      }
    ],
    [
      {
        name           = "チェック失敗"
        expr           = local.q_check_failed_count
        lookback       = 600
        evaluator      = { type = "gt", params = [0] }
        no_data_state  = "OK"
        summary        = "{{ $labels.check_type }} チェックが失敗しました（{{ $labels.error_code }}）"
        severity_label = "critical"
      },
      {
        # 直近15分に check_result が1件もない場合、クエリ結果が空（No Data）になるため
        # No Data をアラート扱いにする
        name           = "チェック途絶"
        expr           = local.q_check_result_count_15m
        lookback       = 900
        evaluator      = { type = "lt", params = [1] }
        no_data_state  = "Alerting"
        summary        = "チェックアプリが停止している可能性があります（直近15分間 check_result が0件）"
        severity_label = "critical"
      },
    ],
  )
}

resource "grafana_rule_group" "device_health" {
  name             = "デバイスデータ監視"
  folder_uid       = grafana_folder.device_health.uid
  interval_seconds = 60 * var.check_interval_minutes

  dynamic "rule" {
    for_each = local.alert_rules
    content {
      name      = rule.value.name
      condition = "C"

      data {
        ref_id         = "A"
        datasource_uid = data.grafana_data_source.loki.uid

        relative_time_range {
          from = rule.value.lookback
          to   = 0
        }

        model = jsonencode({
          datasource    = local.loki_ds
          expr          = rule.value.expr
          queryType     = "instant"
          intervalMs    = 1000
          maxDataPoints = 43200
          refId         = "A"
        })
      }

      data {
        ref_id         = "B"
        datasource_uid = "__expr__"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          datasource = { type = "__expr__", uid = "__expr__" }
          expression = "A"
          reducer    = "last"
          refId      = "B"
          type       = "reduce"
        })
      }

      data {
        ref_id         = "C"
        datasource_uid = "__expr__"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          datasource = { type = "__expr__", uid = "__expr__" }
          conditions = [{
            evaluator = rule.value.evaluator
            operator  = { type = "and" }
            query     = { params = ["C"] }
            reducer   = { params = [], type = "last" }
            type      = "query"
          }]
          expression = "B"
          refId      = "C"
          type       = "threshold"
        })
      }

      no_data_state  = rule.value.no_data_state
      exec_err_state = "Error"

      annotations = {
        summary = rule.value.summary
      }

      labels = {
        severity = rule.value.severity_label
      }

      # 通知先メールがある場合のみ、そこへ直接通知する。ない場合は Grafana の通知ポリシー（既定）に従う
      dynamic "notification_settings" {
        for_each = grafana_contact_point.email
        content {
          contact_point = notification_settings.value.name
        }
      }

      is_paused = false
    }
  }
}
