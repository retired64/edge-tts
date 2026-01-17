#!/usr/bin/env python3
"""
PDF to Audiobook Converter (Platinum Edition)
Compliance: edge-tts Official Best Practices
Features: Param Validation, Timeout Mgmt, Voice Discovery, Streaming.
"""

import argparse
import asyncio
import logging
import re
import sys
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Dependencias de terceros
import edge_tts
import PyPDF2
from edge_tts.communicate import remove_incompatible_characters
from edge_tts.exceptions import NoAudioReceived, UnexpectedResponse, WebSocketError

# ═══════════════════════════════════════
# ⚙️ CONFIGURACIÓN Y LOGGING
# ═══════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("PlatinumTTS")

@dataclass
class AppConfig:
    """Configuración inmutable de la aplicación."""
    input_file: Optional[Path]
    output_file: Optional[Path]
    voice: str
    rate: str
    volume: str
    pitch: str
    connect_timeout: int = 10
    receive_timeout: int = 60

# ═══════════════════════════════════════
# 🛡️ VALIDACIÓN Y UTILIDADES
# ═══════════════════════════════════════

def validate_voice_params(rate: str, volume: str, pitch: str) -> None:
    """
    Valida estrictamente los parámetros según la documentación de edge-tts.
    Evita fallos de API antes de iniciar la conexión.
    """
    # Patrones oficiales de Microsoft
    patterns = {
        "rate": (r"^[+-]\d+%$", "Ej: +10%, -15%"),
        "volume": (r"^[+-]\d+%$", "Ej: +20%, -10%"),
        "pitch": (r"^[+-]\d+Hz$", "Ej: +5Hz, -2Hz")
    }

    for param, value in [("rate", rate), ("volume", volume), ("pitch", pitch)]:
        regex, help_text = patterns[param]
        if not re.match(regex, value):
            raise ValueError(
                f"❌ Parámetro inválido '{param}': {value}\n"
                f"   Formato requerido: {help_text}"
            )

async def list_available_voices(locale_prefix: str = "es-") -> None:
    """Muestra una tabla de voces disponibles filtradas por idioma."""
    print(f"🔍 Buscando voces con prefijo: '{locale_prefix}'...")
    try:
        voices = await edge_tts.list_voices()
        # Filtrar voces latinas/hispanas y neuronales
        filtered = [
            v for v in voices 
            if v['Locale'].startswith(locale_prefix) and "Neural" in v['ShortName']
        ]
        
        print("\n" + "═"*60)
        print(f"{'NOMBRE CORTO':<35} | {'GÉNERO':<10} | {'REGIÓN'}")
        print("─"*60)
        
        for v in filtered:
            print(f"{v['ShortName']:<35} | {v['Gender']:<10} | {v['Locale']}")
            
        print("═"*60 + "\n")
        
    except Exception as e:
        logger.error(f"Error al listar voces: {e}")

# ═══════════════════════════════════════
# 🛠️ LÓGICA DE NEGOCIO (CORE)
# ═══════════════════════════════════════

def extract_clean_text(file_path: Path) -> str:
    """Extrae y sanitiza el texto usando las herramientas oficiales."""
    if not file_path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

    logger.info(f"📖 Leyendo PDF: {file_path.name}")
    text_buffer = []

    try:
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            total = len(reader.pages)
            
            if total == 0: raise ValueError("PDF vacío")

            for i, page in enumerate(reader.pages):
                raw = page.extract_text()
                if raw:
                    # ✅ USO OFICIAL: remove_incompatible_characters
                    clean = remove_incompatible_characters(raw)
                    text_buffer.append(clean)
                
                # Feedback de progreso
                if (i + 1) % 25 == 0:
                    sys.stdout.write(f"\r   ⏳ Extrayendo pág. {i+1}/{total}...")
                    sys.stdout.flush()
            
            sys.stdout.write("\r" + " "*50 + "\r") # Limpiar línea

    except Exception as e:
        logger.error(f"Fallo crítico en lectura de PDF: {e}")
        raise

    # Normalización final de espacios
    full_text = " ".join(text_buffer)
    return re.sub(r'\s+', ' ', full_text).strip()

async def stream_audio(config: AppConfig, text: str) -> bool:
    """Genera audio con streaming y configuración de Timeouts."""
    logger.info(f"📡 Conectando (Timeout: {config.connect_timeout}s)...")
    
    # ✅ USO OFICIAL: Timeouts explícitos y todos los parámetros
    communicate = edge_tts.Communicate(
        text=text,
        voice=config.voice,
        rate=config.rate,
        volume=config.volume,
        pitch=config.pitch,
        connect_timeout=config.connect_timeout,
        receive_timeout=config.receive_timeout
    )

    total_bytes = 0
    start_time = asyncio.get_running_loop().time()

    try:
        with open(config.output_file, "wb") as f:
            # ✅ PATRÓN DE STREAMING: async for
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    data = chunk["data"]
                    f.write(data)
                    total_bytes += len(data)
                    
                    # UX: Barra de progreso dinámica
                    mb = total_bytes / (1024 * 1024)
                    sys.stdout.write(f"\r   💾 Recibiendo: {mb:.2f} MB")
                    sys.stdout.flush()
                
                elif chunk["type"] == "error":
                    logger.error(f"Error remoto: {chunk['message']}")

        sys.stdout.write("\n")
        duration = asyncio.get_running_loop().time() - start_time
        logger.info(f"✅ Finalizado en {duration:.1f}s. Archivo: {config.output_file.name}")
        return True

    # ✅ MANEJO DE EXCEPCIONES ESPECÍFICAS
    except (NoAudioReceived, UnexpectedResponse, WebSocketError) as e:
        sys.stdout.write("\n")
        logger.critical(f"❌ Error de API/Red: {e}")
        if config.output_file.exists():
            config.output_file.unlink() # Limpieza
        return False
    except Exception as e:
        sys.stdout.write("\n")
        logger.critical(f"❌ Error inesperado: {e}")
        return False

# ═══════════════════════════════════════
# 🚀 CLI & MAIN
# ═══════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="Conversor PDF a Audio (Platinum Edition)")
    
    # Grupo de acciones exclusivas (Convertir O Listar voces)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("input", nargs="?", type=Path, help="PDF de entrada")
    group.add_argument("--list-voices", action="store_true", help="Listar voces disponibles")

    # Parámetros opcionales
    parser.add_argument("-o", "--output", type=Path, help="Archivo MP3 salida")
    parser.add_argument("--voice", default="es-MX-DaliaNeural", help="ID de voz")
    parser.add_argument("--rate", default="-15%", help="Velocidad (ej: -15%)")
    parser.add_argument("--volume", default="+0%", help="Volumen (ej: +10%)")
    parser.add_argument("--pitch", default="+0Hz", help="Tono (ej: +5Hz)")

    args = parser.parse_args()

    # 1. Modo Listar Voces
    if args.list_voices:
        await list_available_voices()
        return

    # 2. Modo Conversión
    try:
        # Validación temprana
        validate_voice_params(args.rate, args.volume, args.pitch)
        
        output_path = args.output or args.input.with_suffix('.mp3')
        
        config = AppConfig(
            input_file=args.input,
            output_file=output_path,
            voice=args.voice,
            rate=args.rate,
            volume=args.volume,
            pitch=args.pitch
        )

        clean_text = extract_clean_text(config.input_file)
        if not clean_text:
            logger.warning("⚠️ El PDF no contiene texto procesable.")
            return

        print(f"📊 Texto: {len(clean_text):,} chars | Voz: {config.voice}")
        print("═" * 60)
        
        success = await stream_audio(config, clean_text)
        if not success: sys.exit(1)

    except ValueError as ve:
        logger.error(str(ve))
        sys.exit(1)
    except Exception as e:
        logger.exception("Error fatal:")
        sys.exit(1)

if __name__ == "__main__":
    # Fix para Windows policy
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Cancelado por usuario.")
