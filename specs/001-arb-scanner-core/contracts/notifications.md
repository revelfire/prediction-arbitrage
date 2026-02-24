# Notification Contract: Webhook Payloads

## Slack Webhook

POST to configured `slack_webhook` URL with JSON body:

```json
{
  "text": "Arb Alert: 1.86% spread on \"Will BTC exceed $100k?\"",
  "blocks": [
    {
      "type": "header",
      "text": {"type": "plain_text", "text": "Arbitrage Opportunity Detected"}
    },
    {
      "type": "section",
      "fields": [
        {"type": "mrkdwn", "text": "*Buy:* YES on Polymarket @ $0.62"},
        {"type": "mrkdwn", "text": "*Sell:* NO on Kalshi @ $0.35"},
        {"type": "mrkdwn", "text": "*Net Spread:* 1.86%"},
        {"type": "mrkdwn", "text": "*Max Size:* $150"},
        {"type": "mrkdwn", "text": "*Match Confidence:* 95%"},
        {"type": "mrkdwn", "text": "*Annualized:* 34%"}
      ]
    }
  ]
}
```

## Discord Webhook

POST to configured `discord_webhook` URL with JSON body:

```json
{
  "content": "Arb Alert: 1.86% spread detected",
  "embeds": [
    {
      "title": "Arbitrage Opportunity",
      "color": 3066993,
      "fields": [
        {"name": "Buy", "value": "YES on Polymarket @ $0.62", "inline": true},
        {"name": "Sell", "value": "NO on Kalshi @ $0.35", "inline": true},
        {"name": "Net Spread", "value": "1.86%", "inline": true},
        {"name": "Max Size", "value": "$150", "inline": true},
        {"name": "Match Confidence", "value": "95%", "inline": true},
        {"name": "Annualized Return", "value": "34%", "inline": true}
      ]
    }
  ]
}
```
