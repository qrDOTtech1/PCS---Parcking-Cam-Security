#!/usr/bin/env python3
"""
Script de migration pour mettre à jour la base de données SLEDI v2.0
Exécuter: python migrate.py
"""

import sqlite3
import sys
import os

DB_PATH = "/home/Wlansolo/vigilance_multi.db"


def migrate():
    if not os.path.exists(DB_PATH):
        print(
            "Base de données non trouvée. Elle sera créée automatiquement au premier démarrage."
        )
        return

    print(f"Migration de la base: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    colonnes = [
        ("config_json", "TEXT DEFAULT '{}'"),
        ("recording_enabled", "INTEGER DEFAULT 1"),
        ("stream_auto_mode", "VARCHAR(20) DEFAULT 'off'"),
        ("is_streaming", "INTEGER DEFAULT 0"),
    ]

    for nom_colonne, definition in colonnes:
        try:
            cursor.execute(f"ALTER TABLE camera ADD COLUMN {nom_colonne} {definition}")
            print(f"  + Colonne '{nom_colonne}' ajoutée")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  = Colonne '{nom_colonne}' existe déjà")
            else:
                print(f"  ! Erreur '{nom_colonne}': {e}")

    conn.commit()
    conn.close()

    print("Migration terminée!")


if __name__ == "__main__":
    migrate()
