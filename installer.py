#!/usr/bin/env python3
"""
POS System — Graphical Installation Wizard
==========================================
Leads a distributor through 3 guided steps:
  1. Provisioning  : Consume OTPK → generate .env → patch IMAGE_* tags
  2. Docker Login  : Authenticate with GHCR
  3. Deployment    : Review summary → docker compose up -d with live log

No Docker or Linux knowledge required from the distributor.
"""

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_DIR        = Path(__file__).parent.resolve()
ENV_EXAMPLE     = REPO_DIR / ".env.example"
ENV_FILE        = REPO_DIR / ".env"
PROVISION_PY    = REPO_DIR / "provision.py"
COMPOSE_FILE    = REPO_DIR / "docker-compose.prod.yml"

# ── Colour palette ────────────────────────────────────────────────────────────
C_BRAND     = "#1a1a2e"
C_ACCENT    = "#4a6cf7"
C_SUCCESS   = "#28a745"
C_DANGER    = "#dc3545"
C_INFO      = "#0288d1"

# ── i18n ──────────────────────────────────────────────────────────────────────
# Mirrors React i18next:
#   TRANSLATIONS  ≈ per-locale JSON files       (one dict per language)
#   _LANG         ≈ i18n.language               (currently active locale)
#   t(key)        ≈ the t() hook                (lookup with optional {param})
#   set_lang(lc)  ≈ i18n.changeLanguage()       (switch + UI rebuild)

_LANG: str = "de"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "de": {
        # ── Chrome
        "title":              "POS System  ·  Installations-Assistent",
        "step1_tab":          "  1. Lizenzdaten  ",
        "step2_tab":          "  2. Docker Login  ",
        "step3_tab":          "  3. Deployment  ",
        "btn_back":           "← Zurück",
        "btn_next":           "Weiter →",
        "btn_install":        "Installieren",
        "btn_done":           "Fertig ✓",
        # ── Step 1
        "s1_title":           "Schritt 1 — Lizenzdaten & Image-Tags",
        "s1_desc":            (
            "Geben Sie den einmaligen Provisioning-Token (OTPK) und die Legisell-URL ein.\n"
            "Die Image-Tags erhalten Sie zusammen mit dem OTPK vom Legisell-Owner."
        ),
        "s1_lbl_otpk":        "Provisioning Token (OTPK):",
        "s1_lbl_url":         "Legisell Backend URL:",
        "s1_lbl_output":      "Ausgabe:",
        "s1_err_missing":     "Bitte alle Felder ausfüllen.",
        "s1_log_connecting":  "Verbinde mit {url} \u2026",
        "s1_log_fail":        "\u2717 Provisioning fehlgeschlagen. Bitte Token und URL prüfen.",
        "s1_log_writing":     "Schreibe Image-Tags in .env \u2026",
        "s1_log_tags_ok":     "\u2713 Image-Tags erfolgreich gesetzt.",
        "s1_log_tags_err":    "\u2717 Fehler beim Schreiben der Image-Tags: {exc}",
        "s1_log_done":        "\u2713 Provisioning abgeschlossen! Weiter zum nächsten Schritt.",
        # ── Step 2
        "s2_title":           "Schritt 2 — Docker Registry Login",
        "s2_desc":            (
            "Geben Sie die GHCR-Zugangsdaten ein, die Sie vom Legisell-Owner erhalten haben.\n"
            "Diese werden benötigt, um die Docker-Images herunterzuladen."
        ),
        "s2_lbl_user":        "GHCR Benutzername:",
        "s2_lbl_token":       "GHCR Token / PAT:",
        "s2_show_token":      "Token anzeigen",
        "s2_err_missing":     "Bitte Benutzername und Token eingeben.",
        "s2_connecting":      "Verbinde mit ghcr.io \u2026",
        "s2_no_docker":       "\u2717 'docker' nicht gefunden. Ist Docker installiert?",
        "s2_login_ok":        "\u2713 Login erfolgreich!",
        "s2_login_fail":      "Login fehlgeschlagen.",
        "s2_login_err":       "\u2717 Fehler: {msg}",
        # ── Step 3
        "s3_title":           "Schritt 3 — Zusammenfassung & Deployment",
        "s3_sum_api_url":     "Legisell API URL",
        "s3_sum_ghcr_user":   "GHCR Benutzername",
        "s3_sum_app_name":    "App Name",
        "s3_sum_port":        "Öffentlicher Port",
        "s3_sum_db":          "Datenbank",
        "s3_sum_secrets":     "Secrets",
        "s3_secrets_set":     "\u25cf POSTGRES_PASSWORD  \u25cf REDIS_PASSWORD  \u25cf JWT_SECRET  [gesetzt \u2713]",
        "s3_hint":            'Prüfen Sie die Angaben und klicken Sie auf "Installieren".',
        "s3_lbl_log":         "Deployment-Log:",
        "s3_log_pulling":     "  (Images werden heruntergeladen \u2014 das kann einige Minuten dauern \u2026)\n",
        "s3_no_docker":       "\u2717 FEHLER: 'docker' nicht gefunden. Bitte Docker installieren.",
        "s3_log_success":     "\u2713 Deployment erfolgreich abgeschlossen!",
        "s3_log_url":         "  \u2192 System erreichbar unter: http://localhost:{port}",
        "s3_log_fail":        "\n\u2717 Deployment fehlgeschlagen. Bitte den Log prüfen.",
        "s3_log_tip":         "  Tipp: docker compose -f docker-compose.prod.yml logs",
        # ── Errors / dialogs
        "err_title_missing":  "Fehlende Eingaben",
        "err_prereq_title":   "Fehler beim Start",
        "err_prereq_msg":     "Voraussetzungen nicht erfüllt:\n\n{items}",
        "err_no_provision":   "provision.py nicht gefunden in {dir}",
        "err_no_envexample":  ".env.example nicht gefunden in {dir}",
        "err_no_compose":     "docker-compose.prod.yml nicht gefunden in {dir}",
        "err_no_docker":      "Docker ist nicht installiert oder nicht im PATH.",
    },
    "en": {
        # ── Chrome
        "title":              "POS System  ·  Installation Wizard",
        "step1_tab":          "  1. License Data  ",
        "step2_tab":          "  2. Docker Login  ",
        "step3_tab":          "  3. Deployment  ",
        "btn_back":           "\u2190 Back",
        "btn_next":           "Next \u2192",
        "btn_install":        "Install",
        "btn_done":           "Done \u2713",
        # ── Step 1
        "s1_title":           "Step 1 \u2014 License Data & Image Tags",
        "s1_desc":            (
            "Enter the one-time provisioning token (OTPK) and the Legisell URL.\n"
            "The image tags are provided by the Legisell owner together with the OTPK."
        ),
        "s1_lbl_otpk":        "Provisioning Token (OTPK):",
        "s1_lbl_url":         "Legisell Backend URL:",
        "s1_lbl_output":      "Output:",
        "s1_err_missing":     "Please fill in all fields.",
        "s1_log_connecting":  "Connecting to {url} \u2026",
        "s1_log_fail":        "\u2717 Provisioning failed. Please check token and URL.",
        "s1_log_writing":     "Writing image tags to .env \u2026",
        "s1_log_tags_ok":     "\u2713 Image tags written successfully.",
        "s1_log_tags_err":    "\u2717 Error writing image tags: {exc}",
        "s1_log_done":        "\u2713 Provisioning complete! Proceed to the next step.",
        # ── Step 2
        "s2_title":           "Step 2 \u2014 Docker Registry Login",
        "s2_desc":            (
            "Enter the GHCR credentials provided by the Legisell owner.\n"
            "These are required to pull the Docker images."
        ),
        "s2_lbl_user":        "GHCR Username:",
        "s2_lbl_token":       "GHCR Token / PAT:",
        "s2_show_token":      "Show token",
        "s2_err_missing":     "Please enter username and token.",
        "s2_connecting":      "Connecting to ghcr.io \u2026",
        "s2_no_docker":       "\u2717 'docker' not found. Is Docker installed?",
        "s2_login_ok":        "\u2713 Login successful!",
        "s2_login_fail":      "Login failed.",
        "s2_login_err":       "\u2717 Error: {msg}",
        # ── Step 3
        "s3_title":           "Step 3 \u2014 Summary & Deployment",
        "s3_sum_api_url":     "Legisell API URL",
        "s3_sum_ghcr_user":   "GHCR Username",
        "s3_sum_app_name":    "App Name",
        "s3_sum_port":        "Public Port",
        "s3_sum_db":          "Database",
        "s3_sum_secrets":     "Secrets",
        "s3_secrets_set":     "\u25cf POSTGRES_PASSWORD  \u25cf REDIS_PASSWORD  \u25cf JWT_SECRET  [set \u2713]",
        "s3_hint":            'Review your settings and click "Install".',
        "s3_lbl_log":         "Deployment Log:",
        "s3_log_pulling":     "  (Pulling images \u2014 this may take a few minutes \u2026)\n",
        "s3_no_docker":       "\u2717 ERROR: 'docker' not found. Please install Docker.",
        "s3_log_success":     "\u2713 Deployment completed successfully!",
        "s3_log_url":         "  \u2192 System available at: http://localhost:{port}",
        "s3_log_fail":        "\n\u2717 Deployment failed. Please check the log.",
        "s3_log_tip":         "  Tip: docker compose -f docker-compose.prod.yml logs",
        # ── Errors / dialogs
        "err_title_missing":  "Missing Input",
        "err_prereq_title":   "Startup Error",
        "err_prereq_msg":     "Prerequisites not met:\n\n{items}",
        "err_no_provision":   "provision.py not found in {dir}",
        "err_no_envexample":  ".env.example not found in {dir}",
        "err_no_compose":     "docker-compose.prod.yml not found in {dir}",
        "err_no_docker":      "Docker is not installed or not in PATH.",
    },
    "ru": {
        # ── Chrome
        "title":              "POS System  \u00b7  \u041c\u0430\u0441\u0442\u0435\u0440 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0438",
        "step1_tab":          "  1. \u041b\u0438\u0446\u0435\u043d\u0437\u0438\u044f  ",
        "step2_tab":          "  2. Docker Login  ",
        "step3_tab":          "  3. \u0420\u0430\u0437\u0432\u0451\u0440\u0442\u044b\u0432\u0430\u043d\u0438\u0435  ",
        "btn_back":           "\u2190 \u041d\u0430\u0437\u0430\u0434",
        "btn_next":           "\u0414\u0430\u043b\u0435\u0435 \u2192",
        "btn_install":        "\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c",
        "btn_done":           "\u0413\u043e\u0442\u043e\u0432\u043e \u2713",
        # ── Step 1
        "s1_title":           "\u0428\u0430\u0433 1 \u2014 \u0414\u0430\u043d\u043d\u044b\u0435 \u043b\u0438\u0446\u0435\u043d\u0437\u0438\u0438 \u0438 \u0442\u0435\u0433\u0438 \u043e\u0431\u0440\u0430\u0437\u043e\u0432",
        "s1_desc":            (
            "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043e\u0434\u043d\u043e\u0440\u0430\u0437\u043e\u0432\u044b\u0439 \u0442\u043e\u043a\u0435\u043d (OTPK) \u0438 URL Legisell.\n"
            "\u0422\u0435\u0433\u0438 \u043e\u0431\u0440\u0430\u0437\u043e\u0432 \u043f\u0440\u0435\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u044f\u044e\u0442\u0441\u044f \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0435\u043c Legisell \u0432\u043c\u0435\u0441\u0442\u0435 \u0441 OTPK."
        ),
        "s1_lbl_otpk":        "\u0422\u043e\u043a\u0435\u043d (OTPK):",
        "s1_lbl_url":         "URL Legisell Backend:",
        "s1_lbl_output":      "\u0412\u044b\u0432\u043e\u0434:",
        "s1_err_missing":     "\u041f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430, \u0437\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u0432\u0441\u0435 \u043f\u043e\u043b\u044f.",
        "s1_log_connecting":  "\u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a {url} \u2026",
        "s1_log_fail":        "\u2717 \u041e\u0448\u0438\u0431\u043a\u0430 \u0438\u043d\u0438\u0446\u0438\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u0438. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0442\u043e\u043a\u0435\u043d \u0438 URL.",
        "s1_log_writing":     "\u0417\u0430\u043f\u0438\u0441\u044c \u0442\u0435\u0433\u043e\u0432 \u043e\u0431\u0440\u0430\u0437\u043e\u0432 \u0432 .env \u2026",
        "s1_log_tags_ok":     "\u2713 \u0422\u0435\u0433\u0438 \u043e\u0431\u0440\u0430\u0437\u043e\u0432 \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0437\u0430\u043f\u0438\u0441\u0430\u043d\u044b.",
        "s1_log_tags_err":    "\u2717 \u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0438\u0441\u0438 \u0442\u0435\u0433\u043e\u0432: {exc}",
        "s1_log_done":        "\u2713 \u0418\u043d\u0438\u0446\u0438\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u044f \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430! \u041f\u0435\u0440\u0435\u0445\u043e\u0434\u0438\u0442\u0435 \u043a \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0435\u043c\u0443 \u0448\u0430\u0433\u0443.",
        # ── Step 2
        "s2_title":           "\u0428\u0430\u0433 2 \u2014 \u0412\u0445\u043e\u0434 \u0432 Docker Registry",
        "s2_desc":            (
            "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0443\u0447\u0451\u0442\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 GHCR, \u043f\u043e\u043b\u0443\u0447\u0435\u043d\u043d\u044b\u0435 \u043e\u0442 \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430 Legisell.\n"
            "\u041e\u043d\u0438 \u043d\u0435\u043e\u0431\u0445\u043e\u0434\u0438\u043c\u044b \u0434\u043b\u044f \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 Docker-\u043e\u0431\u0440\u0430\u0437\u043e\u0432."
        ),
        "s2_lbl_user":        "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c GHCR:",
        "s2_lbl_token":       "\u0422\u043e\u043a\u0435\u043d GHCR / PAT:",
        "s2_show_token":      "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0442\u043e\u043a\u0435\u043d",
        "s2_err_missing":     "\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0438\u043c\u044f \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f \u0438 \u0442\u043e\u043a\u0435\u043d.",
        "s2_connecting":      "\u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a ghcr.io \u2026",
        "s2_no_docker":       "\u2717 'docker' \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. \u0423\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d \u043b\u0438 Docker?",
        "s2_login_ok":        "\u2713 \u0412\u0445\u043e\u0434 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d \u0443\u0441\u043f\u0435\u0448\u043d\u043e!",
        "s2_login_fail":      "\u041e\u0448\u0438\u0431\u043a\u0430 \u0432\u0445\u043e\u0434\u0430.",
        "s2_login_err":       "\u2717 \u041e\u0448\u0438\u0431\u043a\u0430: {msg}",
        # ── Step 3
        "s3_title":           "\u0428\u0430\u0433 3 \u2014 \u0421\u0432\u043e\u0434\u043a\u0430 \u0438 \u0440\u0430\u0437\u0432\u0451\u0440\u0442\u044b\u0432\u0430\u043d\u0438\u0435",
        "s3_sum_api_url":     "URL Legisell API",
        "s3_sum_ghcr_user":   "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c GHCR",
        "s3_sum_app_name":    "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u044f",
        "s3_sum_port":        "\u041f\u0443\u0431\u043b\u0438\u0447\u043d\u044b\u0439 \u043f\u043e\u0440\u0442",
        "s3_sum_db":          "\u0411\u0430\u0437\u0430 \u0434\u0430\u043d\u043d\u044b\u0445",
        "s3_sum_secrets":     "\u0421\u0435\u043a\u0440\u0435\u0442\u044b",
        "s3_secrets_set":     "\u25cf POSTGRES_PASSWORD  \u25cf REDIS_PASSWORD  \u25cf JWT_SECRET  [\u0437\u0430\u0434\u0430\u043d\u043e \u2713]",
        "s3_hint":            "\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0438 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c\u00bb.",
        "s3_lbl_log":         "\u0416\u0443\u0440\u043d\u0430\u043b \u0440\u0430\u0437\u0432\u0451\u0440\u0442\u044b\u0432\u0430\u043d\u0438\u044f:",
        "s3_log_pulling":     "  (\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u043e\u0431\u0440\u0430\u0437\u043e\u0432 \u2014 \u044d\u0442\u043e \u043c\u043e\u0436\u0435\u0442 \u0437\u0430\u043d\u044f\u0442\u044c \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u043c\u0438\u043d\u0443\u0442 \u2026)\n",
        "s3_no_docker":       "\u2717 \u041e\u0428\u0418\u0411\u041a\u0410: 'docker' \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. \u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u0435 Docker.",
        "s3_log_success":     "\u2713 \u0420\u0430\u0437\u0432\u0451\u0440\u0442\u044b\u0432\u0430\u043d\u0438\u0435 \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e!",
        "s3_log_url":         "  \u2192 \u0421\u0438\u0441\u0442\u0435\u043c\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u043f\u043e \u0430\u0434\u0440\u0435\u0441\u0443: http://localhost:{port}",
        "s3_log_fail":        "\n\u2717 \u0420\u0430\u0437\u0432\u0451\u0440\u0442\u044b\u0432\u0430\u043d\u0438\u0435 \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0436\u0443\u0440\u043d\u0430\u043b.",
        "s3_log_tip":         "  \u0421\u043e\u0432\u0435\u0442: docker compose -f docker-compose.prod.yml logs",
        # ── Errors / dialogs
        "err_title_missing":  "\u041d\u0435\u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u043d\u044b\u0435 \u043f\u043e\u043b\u044f",
        "err_prereq_title":   "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0443\u0441\u043a\u0430",
        "err_prereq_msg":     "\u041f\u0440\u0435\u0434\u0432\u0430\u0440\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u0443\u0441\u043b\u043e\u0432\u0438\u044f \u043d\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u044b:\n\n{items}",
        "err_no_provision":   "provision.py \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0432 {dir}",
        "err_no_envexample":  ".env.example \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0432 {dir}",
        "err_no_compose":     "docker-compose.prod.yml \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0432 {dir}",
        "err_no_docker":      "Docker \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d \u0438\u043b\u0438 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0432 PATH.",
    },
}


def t(key: str, **kwargs: str) -> str:
    """Look up *key* in the active locale, falling back to the key itself.
    Supports {param} placeholders via keyword arguments — same as React i18next
    interpolation: t("s3_log_url", port="8080")
    """
    text = TRANSLATIONS.get(_LANG, TRANSLATIONS["de"]).get(key, key)
    return text.format(**kwargs) if kwargs else text


def set_lang(code: str) -> None:
    global _LANG
    _LANG = code

# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_env_keys(keys: list[str]) -> dict[str, str]:
    """Parse .env file and return a dict of requested key → value."""
    result: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return result
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            if k in keys:
                # Strip inline comments (e.g. "value # note")
                v = re.sub(r"\s+#.*$", "", v).strip()
                result[k] = v
    return result


def _patch_env_keys(mapping: dict[str, str]) -> None:
    """Replace or append KEY=value entries in .env."""
    content = ENV_FILE.read_text(encoding="utf-8")
    for key, value in mapping.items():
        new_content, n = re.subn(
            rf"^{re.escape(key)}=.*$",
            f"{key}={value}",
            content,
            flags=re.MULTILINE,
        )
        if n > 0:
            content = new_content
        else:
            content += f"\n{key}={value}"
    ENV_FILE.write_text(content, encoding="utf-8")


def _export_env_to_os_environ(env: dict) -> None:
    """Inject .env variables into *env* dict (os.environ copy)."""
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            v = re.sub(r"\s+#.*$", "", v).strip()
            env.setdefault(k.strip(), v)


# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

class InstallerApp:
    _STEP_KEYS = ["step1_tab", "step2_tab", "step3_tab"]

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(t("title"))
        self.root.resizable(True, True)
        self.root.geometry("860x860")
        self.root.minsize(840, 700)
        self.root.configure(bg="#ffffff")

        # Shared state collected across steps
        self._data: dict[str, str] = {}
        self._current_step = 0

        self._build_chrome()
        self._show_step(0)

    # ── Chrome (header + step indicator + nav bar) ────────────────────────────

    def _build_chrome(self) -> None:
        # Header
        hdr = tk.Frame(self.root, bg=C_BRAND)
        hdr.pack(fill=tk.X)
        self._hdr_lbl = tk.Label(
            hdr,
            text=t("title"),
            bg=C_BRAND, fg="white",
            font=("Segoe UI", 15, "bold"),
            pady=16,
        )
        self._hdr_lbl.pack(side=tk.LEFT, padx=24)

        # Language selector (DE / EN / RU) — right side of header
        self._lang_btns: dict[str, tk.Button] = {}
        for code in ("de", "en", "ru"):
            btn = tk.Button(
                hdr,
                text=code.upper(),
                width=4,
                bg=C_ACCENT if code == _LANG else "#3a3a5c",
                fg="white",
                activebackground=C_ACCENT,
                activeforeground="white",
                relief=tk.FLAT,
                font=("Segoe UI", 9, "bold"),
                command=lambda c=code: self._switch_lang(c),
            )
            btn.pack(side=tk.RIGHT, padx=(0, 6), pady=10)
            self._lang_btns[code] = btn

        # Step indicator bar
        self._step_bar = tk.Frame(self.root, bg="#e8eaf6", pady=0)
        self._step_bar.pack(fill=tk.X)
        self._step_lbls: list[tk.Label] = []
        for key in self._STEP_KEYS:
            lbl = tk.Label(
                self._step_bar,
                text=t(key),
                bg="#e8eaf6", fg="#9e9e9e",
                font=("Segoe UI", 10),
                pady=7,
            )
            lbl.pack(side=tk.LEFT)
            self._step_lbls.append(lbl)

        # Navigation bar — packed BEFORE content so it never gets clipped
        nav = tk.Frame(self.root, bg="white", pady=10)
        nav.pack(side=tk.BOTTOM, fill=tk.X)

        # Content area — fills remaining space, scrollable on resize
        self._content = tk.Frame(self.root, bg="white", padx=28, pady=20)
        self._content.pack(fill=tk.BOTH, expand=True)
        self._btn_back = tk.Button(
            nav, text=t("btn_back"), width=12,
            bg="#f0f0f0", relief=tk.FLAT,
            font=("Segoe UI", 10),
            command=self._back,
        )
        self._btn_back.pack(side=tk.LEFT, padx=20)

        self._btn_next = tk.Button(
            nav, text=t("btn_next"), width=18,
            bg=C_ACCENT, fg="white",
            activebackground="#3558e8",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            command=self._next,
        )
        self._btn_next.pack(side=tk.RIGHT, padx=20)

    def _switch_lang(self, code: str) -> None:
        """Change the active language and rebuild the UI — mirrors i18n.changeLanguage()."""
        set_lang(code)
        # Update static chrome labels
        self._hdr_lbl.configure(text=t("title"))
        self.root.title(t("title"))
        for i, key in enumerate(self._STEP_KEYS):
            self._step_lbls[i].configure(text=t(key))
        # Highlight the active language button
        for c, btn in self._lang_btns.items():
            btn.configure(bg=C_ACCENT if c == code else "#3a3a5c")
        # Rebuild current step content + nav button labels
        self._show_step(self._current_step)

    def _update_step_indicator(self) -> None:
        for i, lbl in enumerate(self._step_lbls):
            if i < self._current_step:
                lbl.configure(bg="#c8e6c9", fg="#2e7d32",
                               font=("Segoe UI", 10, "bold"))
            elif i == self._current_step:
                lbl.configure(bg=C_ACCENT, fg="white",
                               font=("Segoe UI", 10, "bold"))
            else:
                lbl.configure(bg="#e8eaf6", fg="#9e9e9e",
                               font=("Segoe UI", 10))

    def _clear_content(self) -> None:
        for w in self._content.winfo_children():
            w.destroy()

    def _show_step(self, step: int) -> None:
        self._current_step = step
        self._update_step_indicator()
        self._clear_content()

        builders = [self._build_step1, self._build_step2, self._build_step3]
        builders[step]()

        self._btn_back.configure(
            text=t("btn_back"),
            state=tk.NORMAL if step > 0 else tk.DISABLED,
        )
        self._btn_next.configure(
            text=t("btn_install") if step == 2 else t("btn_next"),
            bg=C_ACCENT if step < 2 else C_SUCCESS,
            state=tk.NORMAL,
        )

    def _back(self) -> None:
        if self._current_step > 0:
            self._show_step(self._current_step - 1)

    def _next(self) -> None:
        handlers = [self._run_step1, self._run_step2, self._run_step3]
        handlers[self._current_step]()

    # ── Shared logging helper ─────────────────────────────────────────────────

    def _log(
        self,
        widget: scrolledtext.ScrolledText,
        text: str,
        fg: str | None = None,
    ) -> None:
        """Append a line to a ScrolledText log widget (thread-safe via root.after)."""
        def _append():
            widget.configure(state=tk.NORMAL)
            if fg:
                tag = f"_col_{fg.replace('#', '')}"
                widget.tag_configure(tag, foreground=fg)
                widget.insert(tk.END, text + "\n", tag)
            else:
                widget.insert(tk.END, text + "\n")
            widget.see(tk.END)
            widget.configure(state=tk.DISABLED)

        self.root.after(0, _append)

    def _set_nav(self, *, back: bool, next_: bool) -> None:
        """Enable/disable navigation buttons (thread-safe)."""
        def _do():
            self._btn_back.configure(state=tk.NORMAL if back else tk.DISABLED)
            self._btn_next.configure(state=tk.NORMAL if next_ else tk.DISABLED)
        self.root.after(0, _do)

    # ── STEP 1 — Provisioning ─────────────────────────────────────────────────

    def _build_step1(self) -> None:
        c = self._content
        tk.Label(c, text=t("s1_title"),
                 font=("Segoe UI", 13, "bold"), bg="white").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(
            c,
            text=t("s1_desc"),
            bg="white", fg="#555", font=("Segoe UI", 9),
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))

        fields = [
            ("otpk",          t("s1_lbl_otpk"),   False),
            ("api_url",       t("s1_lbl_url"),     False),
            ("image_backend", "IMAGE_BACKEND:",    False),
            ("image_frontend","IMAGE_FRONTEND:",   False),
            ("image_service", "IMAGE_IMAGE_SERVICE:", False),
        ]
        self._s1_vars: dict[str, tk.StringVar] = {}
        for row, (key, label, secret) in enumerate(fields, start=2):
            tk.Label(c, text=label, bg="white", anchor="w",
                     font=("Segoe UI", 10), width=30).grid(
                row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=self._data.get(key, ""))
            entry = tk.Entry(c, textvariable=var, width=48,
                             show="*" if secret else "",
                             font=("Segoe UI", 10),
                             relief=tk.SOLID, bd=1)
            entry.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=4)
            self._s1_vars[key] = var

        tk.Label(c, text=t("s1_lbl_output"), bg="white",
                 font=("Segoe UI", 10, "bold")).grid(
            row=len(fields)+2, column=0, columnspan=3,
            sticky="w", pady=(14, 2))

        self._s1_log = scrolledtext.ScrolledText(
            c, height=11, width=82, font=("Courier", 9),
            state=tk.DISABLED, bg="#fafafa", relief=tk.SOLID, bd=1)
        self._s1_log.grid(row=len(fields)+3, column=0, columnspan=3)
        c.columnconfigure(1, weight=1)

    def _run_step1(self) -> None:
        vals = {k: v.get().strip() for k, v in self._s1_vars.items()}
        missing = [k for k, v in vals.items() if not v]
        if missing:
            messagebox.showerror(t("err_title_missing"), t("s1_err_missing"))
            return

        self._data.update(vals)
        self._set_nav(back=False, next_=False)

        def task() -> None:
            self._log(self._s1_log,
                      t("s1_log_connecting", url=vals["api_url"]), C_INFO)
            cmd = [
                sys.executable, str(PROVISION_PY),
                "--token",       vals["otpk"],
                "--api-url",     vals["api_url"],
                "--env-example", str(ENV_EXAMPLE),
                "--env-output",  str(ENV_FILE),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, cwd=str(REPO_DIR)
            )

            for line in result.stdout.strip().splitlines():
                self._log(self._s1_log, line)

            if result.returncode != 0:
                err = result.stderr.strip() or t("s1_log_fail")
                for line in err.splitlines():
                    self._log(self._s1_log, line, C_DANGER)
                self._log(self._s1_log, t("s1_log_fail"), C_DANGER)
                self._set_nav(back=False, next_=True)
                return

            # Patch IMAGE_* into .env
            self._log(self._s1_log, t("s1_log_writing"))
            try:
                _patch_env_keys({
                    "IMAGE_BACKEND":       vals["image_backend"],
                    "IMAGE_FRONTEND":      vals["image_frontend"],
                    "IMAGE_IMAGE_SERVICE": vals["image_service"],
                })
                self._log(self._s1_log, t("s1_log_tags_ok"), C_SUCCESS)
            except Exception as exc:  # noqa: BLE001
                self._log(self._s1_log,
                          t("s1_log_tags_err", exc=str(exc)), C_DANGER)
                self._set_nav(back=False, next_=True)
                return

            self._log(self._s1_log, t("s1_log_done"), C_SUCCESS)
            self.root.after(600, lambda: self._show_step(1))

        threading.Thread(target=task, daemon=True).start()

    # ── STEP 2 — Docker Login ─────────────────────────────────────────────────

    def _build_step2(self) -> None:
        c = self._content
        tk.Label(c, text=t("s2_title"),
                 font=("Segoe UI", 13, "bold"), bg="white").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(
            c,
            text=t("s2_desc"),
            bg="white", fg="#555", font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 20))

        tk.Label(c, text=t("s2_lbl_user"), bg="white",
                 font=("Segoe UI", 10), width=26, anchor="w").grid(
            row=2, column=0, sticky="w", pady=8)
        self._s2_user = tk.StringVar(value=self._data.get("ghcr_user", ""))
        tk.Entry(c, textvariable=self._s2_user, width=44,
                 font=("Segoe UI", 10), relief=tk.SOLID, bd=1).grid(
            row=2, column=1, sticky="w", padx=(8, 0))

        tk.Label(c, text=t("s2_lbl_token"), bg="white",
                 font=("Segoe UI", 10), width=26, anchor="w").grid(
            row=3, column=0, sticky="w", pady=8)
        self._s2_token = tk.StringVar(value=self._data.get("ghcr_token", ""))
        self._s2_token_entry = tk.Entry(
            c, textvariable=self._s2_token, width=44, show="*",
            font=("Segoe UI", 10), relief=tk.SOLID, bd=1)
        self._s2_token_entry.grid(row=3, column=1, sticky="w", padx=(8, 0))

        self._s2_show_token = tk.BooleanVar(value=False)
        tk.Checkbutton(
            c, text=t("s2_show_token"),
            variable=self._s2_show_token,
            command=self._toggle_token_visibility,
            bg="white", font=("Segoe UI", 9),
        ).grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(2, 0))

        self._s2_status = tk.Label(
            c, text="", bg="white", font=("Segoe UI", 10),
            wraplength=720, justify=tk.LEFT)
        self._s2_status.grid(row=6, column=0, columnspan=3,
                              sticky="w", pady=(24, 0))
        c.columnconfigure(1, weight=1)

    def _toggle_token_visibility(self) -> None:
        self._s2_token_entry.configure(
            show="" if self._s2_show_token.get() else "*"
        )

    def _run_step2(self) -> None:
        user  = self._s2_user.get().strip()
        token = self._s2_token.get().strip()
        if not user or not token:
            messagebox.showerror(t("err_title_missing"), t("s2_err_missing"))
            return

        self._data["ghcr_user"]  = user
        self._data["ghcr_token"] = token
        self._set_nav(back=False, next_=False)
        self.root.after(0, lambda: self._s2_status.configure(
            text=t("s2_connecting"), fg=C_INFO))

        def task() -> None:
            try:
                result = subprocess.run(
                    ["docker", "login", "ghcr.io",
                     "-u", user, "--password-stdin"],
                    input=token,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                self.root.after(0, lambda: self._s2_status.configure(
                    text=t("s2_no_docker"), fg=C_DANGER))
                self._set_nav(back=True, next_=True)
                return

            combined = (result.stdout + result.stderr).strip()
            success  = result.returncode == 0 and (
                "Login Succeeded" in combined
                or "Login succeeded" in combined
            )

            if success:
                self.root.after(0, lambda: self._s2_status.configure(
                    text=t("s2_login_ok"), fg=C_SUCCESS))
                self.root.after(600, lambda: self._show_step(2))
            else:
                msg = combined or t("s2_login_fail")
                self.root.after(0, lambda: self._s2_status.configure(
                    text=t("s2_login_err", msg=msg), fg=C_DANGER))
                self._set_nav(back=True, next_=True)

        threading.Thread(target=task, daemon=True).start()

    # ── STEP 3 — Summary & Deployment ─────────────────────────────────────────

    def _build_step3(self) -> None:
        c = self._content
        tk.Label(c, text=t("s3_title"),
                 font=("Segoe UI", 13, "bold"), bg="white").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        env = _read_env_keys([
            "APP_NAME", "POS_PUBLIC_PORT",
            "POSTGRES_DB", "POSTGRES_SERVER",
            "IMAGE_BACKEND", "IMAGE_FRONTEND", "IMAGE_IMAGE_SERVICE",
        ])

        summary = [
            (t("s3_sum_api_url"),    self._data.get("api_url", "—")),
            (t("s3_sum_ghcr_user"),  self._data.get("ghcr_user", "—")),
            (t("s3_sum_app_name"),   env.get("APP_NAME", "—")),
            (t("s3_sum_port"),       env.get("POS_PUBLIC_PORT", "80")),
            (t("s3_sum_db"),         f"{env.get('POSTGRES_DB', '—')} @ {env.get('POSTGRES_SERVER', '—')}"),
            ("IMAGE_BACKEND",        env.get("IMAGE_BACKEND", "—")),
            ("IMAGE_FRONTEND",       env.get("IMAGE_FRONTEND", "—")),
            ("IMAGE_IMAGE_SERVICE",  env.get("IMAGE_IMAGE_SERVICE", "—")),
            (t("s3_sum_secrets"),    t("s3_secrets_set")),
        ]

        box = tk.Frame(c, bg="#f0f4ff", relief=tk.RIDGE, bd=1,
                       padx=16, pady=12)
        box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        for i, (k, v) in enumerate(summary):
            tk.Label(box, text=k + ":", bg="#f0f4ff", anchor="w", width=26,
                     font=("Segoe UI", 9, "bold")).grid(
                row=i, column=0, sticky="w", pady=2)
            tk.Label(box, text=v, bg="#f0f4ff", anchor="w",
                     font=("Segoe UI", 9), wraplength=500,
                     justify=tk.LEFT).grid(
                row=i, column=1, sticky="w", padx=(8, 0))

        tk.Label(
            c,
            text=t("s3_hint"),
            bg="white", fg="#555", font=("Segoe UI", 9),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 6))

        tk.Label(c, text=t("s3_lbl_log"), bg="white",
                 font=("Segoe UI", 10, "bold")).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 2))

        self._s3_log = scrolledtext.ScrolledText(
            c, height=15, width=82, font=("Courier", 9),
            state=tk.DISABLED,
            bg="#0d1117", fg="#c9d1d9",
            insertbackground="white",
            relief=tk.SOLID, bd=1,
        )
        self._s3_log.grid(row=4, column=0, columnspan=2)
        c.columnconfigure(1, weight=1)

    def _run_step3(self) -> None:
        self._set_nav(back=False, next_=False)

        def task() -> None:
            self._log(self._s3_log,
                      "▶ docker compose -f docker-compose.prod.yml up -d", "#7ec8e3")
            self._log(self._s3_log, t("s3_log_pulling"), "#aaaaaa")

            env = os.environ.copy()
            _export_env_to_os_environ(env)

            cmd = [
                "docker", "compose",
                "-f", str(COMPOSE_FILE),
                "up", "-d",
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(REPO_DIR),
                    env=env,
                )
            except FileNotFoundError:
                self._log(self._s3_log, t("s3_no_docker"), C_DANGER)
                self._set_nav(back=True, next_=False)
                return

            assert proc.stdout is not None
            for line in proc.stdout:
                self._log(self._s3_log, line.rstrip())
            proc.wait()

            if proc.returncode == 0:
                port = _read_env_keys(["POS_PUBLIC_PORT"]).get(
                    "POS_PUBLIC_PORT", "80")
                self._log(self._s3_log, "")
                self._log(self._s3_log, t("s3_log_success"), C_SUCCESS)
                self._log(self._s3_log,
                          t("s3_log_url", port=port), "#7ec8e3")

                def _finish():
                    self._btn_next.configure(
                        text=t("btn_done"),
                        state=tk.NORMAL,
                        bg=C_SUCCESS,
                        command=self.root.destroy,
                    )
                self.root.after(0, _finish)
            else:
                self._log(self._s3_log, t("s3_log_fail"), C_DANGER)
                self._log(self._s3_log, t("s3_log_tip"), "#aaaaaa")
                self._set_nav(back=True, next_=True)

        threading.Thread(target=task, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _check_prerequisites() -> list[str]:
    """Return a list of human-readable problems, empty if all OK."""
    problems: list[str] = []
    if not PROVISION_PY.is_file():
        problems.append(t("err_no_provision", dir=str(REPO_DIR)))
    if not ENV_EXAMPLE.is_file():
        problems.append(t("err_no_envexample", dir=str(REPO_DIR)))
    if not COMPOSE_FILE.is_file():
        problems.append(t("err_no_compose", dir=str(REPO_DIR)))
    try:
        subprocess.run(["docker", "--version"],
                       capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        problems.append(t("err_no_docker"))
    return problems


def main() -> None:
    # Run a short prerequisite check before opening the GUI
    problems = _check_prerequisites()

    root = tk.Tk()
    root.withdraw()  # hide until ready

    if problems:
        msg = t("err_prereq_msg", items="\n".join(f"  \u2022 {p}" for p in problems))
        messagebox.showerror(t("err_prereq_title"), msg, parent=root)
        root.destroy()
        sys.exit(1)

    root.deiconify()
    InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
