# ShadyFlags

Temporary warning/flag system with ML-based risk scoring.

## Overview

ShadyFlags provides a warning system with auto-flagging based on account age and ML-powered risk assessment. It integrates with ShadyAlts to track suspicious account networks.

## Setup

### 1. Load the Cog
```
[p]load shadyflags
```

### 2. Configure Settings
Configure via the settings commands to set up mod channels, auto-flag thresholds, and moderator roles.

## Features

### Manual Flags
- Create temporary warnings on users
- Set expiry dates
- Track flag reasons and history

### Auto-Flagging
- Flag accounts based on age thresholds
- Configurable age buckets (0-24h, 1-7d, etc.)
- Auto-flag brand new accounts joining

### ML Risk Scoring
When sklearn is available:
- Extracts features from new member joins
- Analyzes username patterns (entropy, random suffixes)
- Considers account age and avatar presence
- Integrates with ShadyAlts network data
- Provides risk scores for suspect identification

### Feature Extraction
On member join, extracts:
- Account age and age bucket
- Avatar presence (custom vs default)
- Username analysis (length, entropy, patterns)
- Display name matching
- Server join velocity
- Alt network information

## Integration with ShadyAlts

- Checks if joining user is in an alt network
- Considers network toxicity (% confirmed bad)
- Auto-flags users with toxic alts
- Promotes suspects to confirmed when banned

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything |
| Moderate Members | Manage flags |
| Configured Mod Roles | Manage flags |

## Technical Notes

- Uses scikit-learn for ML models (optional)
- Falls back to rule-based scoring if sklearn unavailable
- Features stored only for suspects
- Labels only applied when confirmed bad (never "good")
