# Arquitectura, Ingeniería de Software y Guía de Desarrollo para un Plugin de YouTube Music en Decky Loader para SteamOS

## Fundamentos Tecnológicos de Decky Loader y Selección Arquitectónica

El ecosistema homebrew de la consola portátil Steam Deck se fundamenta en gran medida en **Decky Loader** (anteriormente conocido como PluginLoader), un middleware de código abierto desarrollado en Python y React.

Este componente actúa como un inyector dinámico que altera el comportamiento del cliente de Steam inyectando componentes gráficos dentro del **Chromium Embedded Framework (CEF)**, que gestiona la interfaz del modo de juego (Gaming Mode).

La interacción entre el frontend visual (React) y las capacidades de bajo nivel del sistema operativo basado en Linux (SteamOS) se realiza a través de un puente de comunicación bidireccional asíncrono implementado mediante sockets web.

> ✅ **Ventaja clave**: Decky Loader elude completamente las políticas CORS, permitiendo peticiones directas a los servidores de Google y YouTube Music sin necesidad de proxies intermedios.

### Enfoques de Diseño Arquitectónico

| Enfoque Arquitectónico | Mecanismo Operativo | Ventajas Técnicas | Limitaciones del Sistema |
|------------------------|-------------------|------------------|-------------------------|
| **Controlador de Media Genérico (vía MPRIS)** | Interactúa a través de D-Bus con reproductores Flatpak (Spotify, Strawberry, Firefox) | Menor consumo de memoria; desarrollo simplificado al no requerir lógica de reproducción interna | Requiere que el usuario configure, inicie y mantenga la aplicación Flatpak abierta manualmente en Gaming Mode |
| **Reproductor Nativo Integrado** | Ejecuta un servicio en segundo plano en Python que gestiona búsqueda con `ytmusicapi`, extracción con `yt-dlp` y renderizado con `libmpv` | Experiencia de usuario directa desde la interfaz de Steam, sin configuraciones complejas ni cambio de ventana | Exige optimización cuidadosa de recursos para no degradar el rendimiento gráfico de los videojuegos |

> 🎯 **Recomendación**: Para YouTube Music, el enfoque nativo integrado proporciona la experiencia de usuario más refinada, funcionando directamente en segundo plano durante las sesiones de juego.

---

## Estructura del Entorno de Desarrollo y Requisitos del Sistema

### Requisitos Obligatorios

```bash
• Node.js: Versión 16.14+
• pnpm: Versión 9 (explícita, para compatibilidad con CI de Decky Loader)
• Docker: Opcional, pero necesario para compilar binarios nativos C/C++ para arquitectura x86_64
```

### Organización del Repositorio

```
youtube-music-plugin/
├── .vscode/
│   ├── defsettings.json
│   └── settings.json
├── backend/
│   ├── src/
│   ├── out/
│   ├── defaults/
│   └── dist/
├── index.js
├── bin/
├── package.json
├── plugin.json
├── main.py
├── decky.pyi
└── LICENSE
```

### Configuración de `plugin.json`

```json
{
  "name": "YouTube Music Player",
  "author": "Desarrollador",
  "flags": ["debug"],
  "api_version": 1,
  "publish": {
    "tags": [],
    "description": "Reproductor nativo de YouTube Music en segundo plano para SteamOS",
    "image": "assets/store-preview.png"
  }
}
```

> ⚠️ **Notas importantes**:
> - `"flags": ["debug"]`: Activa recarga en caliente y herramientas de depuración remota
> - `"api_version": 1`: Indica a Decky Loader usar APIs WebSocket modernas de `@decky/api`

---

## Arquitectura de Software del Reproductor Multimedia

### Topología de Integración de Componentes

```
[Frontend React] 
       │
       ▼ (Invocación asíncrona mediante call() de @decky/api)
[Backend Python]
       │
       ├──► [ytmusicapi] ► Consultas HTTPS / Autenticación
       │
       ├──► [yt-dlp] ► Resolución dinámica de flujo m3u8 / audio directo
       │
       └──► [python-mpv] ► Control de audio por ctypes / libmpv
```

### Frontend de React (`@decky/api` y `@decky/ui`)

- La interfaz gráfica reside exclusivamente en el **Quick Access Menu (QAM)** de la consola
- Utiliza `@decky/ui` para controles adaptados a pantalla táctil y mandos físicos
- **No realiza** procesamiento de audio ni resolución de streaming directamente
- Se limita a enviar instrucciones serializadas y recibir actualizaciones de estado vía WebSocket

### Backend en Python

Núcleo lógico del plugin. Al cargarse la clase estática `Plugin`, se inicializan tres componentes:

| Componente | Función | Referencia |
|------------|---------|-----------|
| `ytmusicapi` | Emula peticiones HTTP del cliente web oficial de YouTube Music. Permite búsquedas, metadatos de álbumes y acceso a biblioteca privada | [11] |
| `yt-dlp` | Utilidad CLI para extracción de flujos de audio/video. Analiza `videoId` y obtiene URL de transmisión directa (`bestaudio`) | [26] |
| `python-mpv` | Enlace basado en ctypes para libmpv de C. Controla reproducción, volumen, pausas y playlists desde Python | [12] |

---

## Integración de Salida de Audio en PipeWire

SteamOS utiliza **PipeWire** como servidor de sonido por defecto. Al inicializar la instancia de `mpv` mediante Python, se debe asegurar el enrutamiento correcto para evitar:

- Problemas de sincronización de reloj
- Picos de latencia de audio que interrumpan la música durante juegos con alto consumo de CPU

```python
# Configuración crítica de MPV para SteamOS
self.player = mpv.MPV(
    video=False,                    # Desactivar renderizado de video para ahorrar GPU
    ytdl=False,                     # Desactivar resolvedor ytdl integrado de mpv
    input_default_bindings=False,   # Evitar conflictos con mapeos de teclas de SteamOS
    input_vo_keyboard=False,        # Desactivar escucha de eventos de teclado
    ao="pipewire",                  # Enrutar audio nativamente mediante PipeWire
    pipewire_buffer=50              # Buffer de 50ms para prevenir microcortes
)
```

---

## Implementación Detallada del Backend en Python

### Estructura Principal de `main.py`

```python
import os
import sys
import logging
import subprocess
import json
from decky import SettingsManager

# Añadir directorio 'defaults' al path para cargar dependencias empaquetadas
PLUGIN_DIR = os.environ.get("DECKY_PLUGIN_DIR", 
    "/home/deck/homebrew/plugins/youtube-music-plugin")
sys.path.append(os.path.join(PLUGIN_DIR, "defaults"))

# Importar dependencias del plugin
try:
    from ytmusicapi import YTMusic
    import mpv
except ImportError as e:
    logging.error(f"Fallo al importar dependencias de empaquetado local: {str(e)}")


class Plugin:
    async def _main(self):
        """
        Método de inicialización del ciclo de vida del backend.
        Reemplaza al constructor estándar __init__ y se ejecuta en el bucle asíncrono.
        """
        logging.info("Inicializando el backend de YouTube Music Player...")
        
        # Inicialización del gestor de configuraciones persistentes
        self.settings = SettingsManager(
            name="youtube_music",
            settings_directory=os.environ.get("DECKY_PLUGIN_SETTINGS_DIR")
        )
        self.settings.read()
        
        # Instanciar cliente de API con soporte para cookies de autenticación
        cookie_path = os.path.join(
            os.environ.get("DECKY_PLUGIN_SETTINGS_DIR"), 
            "cookie.txt"
        )
        if os.path.exists(cookie_path):
            self.yt_client = YTMusic(auth=cookie_path)
        else:
            self.yt_client = YTMusic()  # Acceso público sin autenticación
        
        # Configurar motor de reproducción MPV con optimizaciones para SteamOS
        self.player = mpv.MPV(
            video=False,
            ytdl=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            ao="pipewire",
            pipewire_buffer=50
        )
        logging.info("Backend de reproducción inicializado correctamente.")

    async def search_songs(self, query: str):
        """
        Realiza una búsqueda de canciones en YouTube Music utilizando el cliente oficial.
        """
        try:
            # Filtrar explícitamente por tipo 'songs' para evitar recuperación de álbumes
            results = self.yt_client.search(query=query, filter="songs")
            
            formatted_results = []
            for item in results[:10]:
                formatted_results.append({
                    "videoId": item.get("videoId"),
                    "title": item.get("title"),
                    "artist": ",".join([a.get("name") for a in item.get("artists", [])]),
                    "thumbnail": item.get("thumbnails", [{}]).get("url")
                })
            return formatted_results
            
        except Exception as e:
            logging.error(f"Error en la búsqueda de YouTube Music: {str(e)}")
            return []

    async def play_track(self, video_id: str):
        """
        Resuelve el flujo de transmisión utilizando yt-dlp local y arranca la reproducción.
        """
        try:
            track_url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Ejecución controlada de yt-dlp para extraer URL del flujo de audio directo
            yt_dlp_path = os.path.join(PLUGIN_DIR, "bin", "yt-dlp")
            if not os.path.exists(yt_dlp_path):
                yt_dlp_path = "yt-dlp"  # Buscar en PATH global de SteamOS
            
            cmd = [
                yt_dlp_path,
                "-g",  # Retornar únicamente la URL del flujo sin descargar
                track_url
            ]
            
            # Lanzar subproceso capturando salida estándar
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True
            )
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                logging.error(f"Error de ejecución en yt-dlp: {stderr}")
                return False
            
            stream_url = stdout.strip()
            
            # Detener cualquier reproducción previa e inyectar el nuevo flujo en MPV
            self.player.loadfile(stream_url)
            return True
            
        except Exception as e:
            logging.error(f"Fallo crítico en el enrutamiento de la pista de audio: {str(e)}")
            return False

    async def control_playback(self, action: str):
        """
        Controla de forma síncrona los estados de reproducción del motor de MPV.
        """
        if not hasattr(self, "player"):
            return False
            
        if action == "pause":
            self.player.pause = True
        elif action == "resume":
            self.player.pause = False
        elif action == "stop":
            self.player.stop()
        return True

    async def _unload(self):
        """
        Destructor asíncrono ejecutado obligatoriamente cuando Decky detiene el plugin.
        """
        logging.info("Deteniendo el motor de audio MPV y liberando recursos del sistema.")
        if hasattr(self, "player"):
            self.player.terminate()
```

---

## Gestión de Autenticación de YouTube Music

Las peticiones sin autenticar están severamente restringidas por políticas anti-bots de Google, resultando frecuentemente en bloqueos HTTP 403.

### Métodos de Autenticación

| Método | Archivo de Destino | Procedimiento de Obtención | Limitaciones |
|--------|-------------------|---------------------------|-------------|
| **Flujo OAuth** | `oauth.json` | Ejecución de inicialización desde terminal local autenticando mediante código de verificación web de Google | No es completamente realizable directamente desde Game Mode sin periféricos físicos de teclado/ratón |
| **Inyección de Cookies de Navegador** | `cookie.txt` | Acceder a `music.youtube.com`, abrir DevTools (F12), ubicar petición HTTP POST saliente y copiar la cabecera `Cookie` completa | Las cookies tienen período de validez temporal y deben ser reinyectadas manualmente al caducar la sesión |

---

## Mantenimiento de la Utilidad `yt-dlp`

> ⚠️ **Advertencia**: YouTube altera regularmente su código JavaScript para interrumpir herramientas de scraping. Es común que la resolución de streams falle con errores de descodificación o accesos denegados.

### Procedimiento de Actualización Manual

Ante fallos generalizados de reproducción, el usuario debe:

1. Descargar el ejecutable binario actualizado de `yt-dlp` desde su [repositorio oficial](https://github.com/yt-dlp/yt-dlp/releases)
2. Reemplazar manualmente el archivo en:
   ```
   /home/deck/homebrew/plugins/youtube-music-plugin/bin/yt-dlp
   ```

---

## Implementación de la Interfaz de Usuario en React

### Control de Coexistencia de Audio

```typescript
import {
  definePlugin,
  ServerAPI,
  call,
} from "@decky/api";
import {
  PanelSection,
  PanelSectionRow,
  Button,
  TextField,
  Focusable,
  staticClasses,
} from "@decky/ui";
import React, { useState } from "react";
import { FaPlay, FaPause, FaSearch } from "react-icons/fa";

interface Track {
  videoId: string;
  title: string;
  artist: string;
  thumbnail: string;
}

const YouTubeMusicPanel: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [tracks, setTracks] = useState<Track[]>([]);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [currentTrack, setCurrentTrack] = useState<Track | null>(null);

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    try {
      // Invocación asíncrona hacia el método search_songs definido en main.py
      const results = await call<string, Track[]>("search_songs", searchQuery);
      setTracks(results);
    } catch (error) {
      console.error("Error al ejecutar la búsqueda asíncrona de canciones:", error);
    }
  };

  const handlePlay = async (track: Track) => {
    try {
      // Asegurar coexistencia de audio: detener música de fondo del sistema
      if (window.AUDIOLOADER_MENUMUSIC) {
        window.AUDIOLOADER_MENUMUSIC.pause();
      }
      
      const success = await call<{ video_id: string }, boolean>("play_track", { 
        video_id: track.videoId 
      });
      
      if (success) {
        setCurrentTrack(track);
        setIsPlaying(true);
      }
    } catch (error) {
      console.error("Fallo al invocar el flujo de reproducción de audio:", error);
    }
  };

  const handlePauseToggle = async () => {
    const action = isPlaying ? "pause" : "resume";
    try {
      const success = await call<{ action: string }, boolean>("control_playback", { action });
      if (success) {
        setIsPlaying(!isPlaying);
      }
    } catch (error) {
      console.error("Fallo al controlar el estado de reproducción:", error);
    }
  };

  return (
    <PanelSection title="YouTube Music">
      <PanelSectionRow>
        <TextField
          label="Buscar en YouTube Music"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <Button onClick={handleSearch} style={{ marginTop: "5px" }}>
          <FaSearch /> Buscar
        </Button>
      </PanelSectionRow>

      {currentTrack && (
        <PanelSectionRow>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 0" }}>
            <img
              src={currentTrack.thumbnail}
              alt="Cover"
              style={{ width: "50px", height: "50px", borderRadius: "4px" }}
            />
            <div style={{ flex: 1, overflow: "hidden" }}>
              <div className={staticClasses.Title} style={{ 
                fontSize: "14px", 
                textOverflow: "ellipsis", 
                overflow: "hidden", 
                whiteSpace: "nowrap" 
              }}>
                {currentTrack.title}
              </div>
              <div style={{ fontSize: "12px", opacity: 0.7 }}>{currentTrack.artist}</div>
            </div>
            <Button onClick={handlePauseToggle}>
              {isPlaying ? <FaPause /> : <FaPlay />}
            </Button>
          </div>
        </PanelSectionRow>
      )}

      <PanelSectionRow title="Resultados de la Búsqueda">
        {tracks.map((track) => (
          <Focusable
            key={track.videoId}
            style={{ 
              padding: "8px", 
              borderBottom: "1px solid rgba(255,255,255,0.1)", 
              display: "flex", 
              cursor: "pointer" 
            }}
            onClick={() => handlePlay(track)}
          >
            <div>
              <div style={{ fontWeight: "bold" }}>{track.title}</div>
              <div style={{ fontSize: "12px", opacity: 0.8 }}>{track.artist}</div>
            </div>
          </Focusable>
        ))}
      </PanelSectionRow>
    </PanelSection>
  );
};

export default definePlugin((serverAPI: ServerAPI) => {
  return {
    title: <div className={staticClasses.Title}>YouTube Music</div>,
    content: <YouTubeMusicPanel />,
    icon: <FaPlay />,
    alwaysRender: true, // Forzar retención en memoria al cerrar el menú QAM
  };
});
```

### Protocolo de Coexistencia de Audio

> 🔊 **Importante**: El plugin oficial **AudioLoader** reproduce música de fondo continuamente cuando no hay juegos activos. Para evitar cacofonía:

```typescript
// Intervención proactiva del frontend para pausar música del sistema
if (window.AUDIOLOADER_MENUMUSIC) {
  window.AUDIOLOADER_MENUMUSIC.pause();
}
```

Nuestro componente React debe invocar `window.AUDIOLOADER_MENUMUSIC` para:
1. Pausar y silenciar flujos del sistema al iniciar YouTube Music
2. Restaurar volumen original al detener explícitamente el flujo multimedia

---

## Ciclo de Construcción, Despliegue y Pruebas Físicas

### Configuración SSH y Automatización

1. **Habilitar Modo Desarrollador** en parámetros del sistema de la consola
2. **Establecer contraseña de administración** desde Konsole:
   ```bash
   passwd
   ```
3. **Configurar claves SSH** para evitar ingreso repetitivo de contraseñas

### Archivo `.vscode/settings.json`

```json
{
  "deckip": "192.168.1.128",
  "deckuser": "deck",
  "deckpass": "contraseña_temporal_deck",
  "deckkey": "~/.ssh/deck_rsa_key"
}
```

### Tareas Preconfiguradas de VS Code

| Tarea | Función |
|-------|---------|
| `Build Task` | Invoca Rollup mediante Node.js, empaquetando código en ZIP dentro de `./out` |
| `builddeploy` | Transpila frontend, compila binarios backend y transfiere vía SSH/rsync a `/home/deck/homebrew/plugins/` |
| `builddeployrestart` | Ejecuta compilación + despliegue + reinicio asíncrono de Decky Loader (`systemctl restart PluginLoader`) |

---

## Protocolo de Depuración del Entorno Chromium (CEF Debugging)

### Acceso al Depurador Remoto

1. Abrir navegador Chromium en la máquina host
2. Navegar a: `chrome://inspect/#devices`
3. Seleccionar el contexto **SharedJSContext** (espacio unificado de ejecución JavaScript para plugins)

### Contextos de Depuración CEF

| Contexto | Puerto | Propósito |
|----------|--------|-----------|
| **SharedJSContext** | 8081 (Remoto) / 8080 (Local) | Canal primario de ejecución del plugin. Inspección de eventos de red e intercambio de datos vía WebSockets |
| **QuickAccess_uid** | 8081 (Remoto) / 8080 (Local) | Analizar árbol DOM del panel QAM para validar estilos CSS y eventos táctiles |
| **Konsole (Syslog)** | SSH / Terminal Nativo | Ejecutar `journalctl -f` para monitoreo de logs del sistema |

> ⚠️ **Advertencia de colisión de puertos**: Software como Syncthing usa por defecto el puerto 8080. Configurar manualmente a puertos alternativos (ej. 8384) para evitar interrupciones en herramientas de desarrollo.

---

## Políticas de Distribución Oficial, Licenciamiento y Cumplimiento Ético

### Flujo de Aprobación para la Tienda de Decky

```
¿Utiliza código generado por IA?
│
├─► SÍ 
│   │
│   ├─► NO APTO para Tienda Oficial
│   └─► Distribución Manual (ZIP de GitHub)
│       └─► Riesgo de advertencias de seguridad
│
└─► NO
    │
    ├─► APTO para Revisión
    │
    ├─► Licencia: Incluir LICENSE con encabezado personalizado + pie de plantilla
    │
    ├─► Monetización: Código abierto y libre
    │
    └─► Estructura: Backend en backend/src, compilación en backend/out
```

### Política de Rechazo de Código Autogenerado mediante IA

> 🚫 **Postura oficial**: El comité de Decky Loader rechaza absolutamente cualquier software con porciones significativas de código escritas o asistidas mediante herramientas de IA (ChatGPT, Claude, GitHub Copilot, etc.).

**Justificación**:
- Licencias GNU/GPL que rigen el ecosistema Decky Loader
- Sistemas generativos indexan bases de código de desarrolladores FOSS sin consentimiento, atribución ni compensación
- Infringe bases éticas y de propiedad del desarrollo de código libre

> 📋 **Caso práctico**: El plugin `decky-youtube-music` del desarrollador `artistro08` fue denegado para inclusión oficial por haber utilizado asistencia de Claude de Anthropic. Su distribución quedó limitada a descargas manuales en GitHub externo.

### Estándares de Licenciamiento y Distribución

#### Requisitos Obligatorios para Publicación

1. **Archivo LICENSE**: Debe existir en el directorio raíz con nombre explícito `LICENSE` o `LICENSE.md`
2. **Estructura del archivo LICENSE**:
   - Parte superior: Términos de la licencia elegida (GPL-3.0 o MIT)
   - Parte inferior: Licencia original de la plantilla base (sin alteraciones)

#### Pautas de Monetización y Accesibilidad

| Categoría | Política |
|-----------|----------|
| **Funciones de Accesibilidad** | ❌ Prohibido incorporar muros de pago o suscripciones para plugins que mitiguen quejas de hardware/software que impacten accesibilidad (discapacidades auditivas, visuales, daltonismo) |
| **Monetización de Servicios Externos** | ✅ Permitido únicamente si el desarrollador debe costear mantenimiento de servidores remotos o APIs comerciales externas de alto costo |
| **Estructuración de Binarios en CI** | ✅ Backend en `./backend/src`; binarios compilados en `./backend/out`. Incumplimiento = fallo en instalación remota |

---

## Referencias Bibliográficas

1. Deckbrew - Decky Loader. https://wiki.deckbrew.xyz/
2. Note for HoloISO Users - Deckbrew. https://wiki.deckbrew.xyz/en/user-guide/home
3. Decky Loader download | SourceForge.net. https://sourceforge.net/projects/decky-loader.mirror/
4. Decky Loader - Steam Deck Homebrew. https://decky.xyz/
5. decky-syncthing/NOTES.md. https://github.com/Azure-Agst/decky-syncthing/blob/main/NOTES.md
6. Migrating to the new decky API - Deckbrew. https://wiki.deckbrew.xyz/en/plugin-dev/new-api-migration
7. SteamDeckHomebrew/decky-loader. https://github.com/steamdeckhomebrew/decky-loader
8. mirobouma/MusicControl. https://github.com/mirobouma/MusicControl
9. Decky Loader Plugin Search - Music: r/SteamDeck. https://www.reddit.com/r/SteamDeck/comments/18ixfcg/decky_loader_plugin_search_music/
10. Decky Music Control YT Music: r/SteamDeck. https://www.reddit.com/r/SteamDeck/comments/1grpcm4/decky_music_control_yt_music/
11. sigma67/ytmusicapi: Unofficial API for YouTube Music. https://github.com/sigma67/ytmusicapi
12. python-mpv - PyPI. https://pypi.org/project/python-mpv/
13. Adding Audio Streaming from Youtube in SUSI Linux. https://blog.fossasia.org/adding-audio-streaming-from-youtube-in-susi-linux/
14. I created a YouTube Music Decky Plugin: r/SteamDeck. https://www.reddit.com/r/SteamDeck/comments/1rt80h7/i_created_a_youtube_music_decky_plugin/
15. Python-mpv not loading correctly a Youtube livestream. https://www.reddit.com/r/youtubedl/comments/1n6rh05/pythonmpv_not_loading_correctly_a_youtube/
16. decky-plugin-template - Codesandbox. https://codesandbox.io/p/github/SteamDeckHomebrew/decky-plugin-template
17. Testing your plugin? · Discussion #298. https://github.com/SteamDeckHomebrew/decky-loader/discussions/298
18. SteamDeckHomebrew/decky-plugin-template. https://github.com/SteamDeckHomebrew/decky-plugin-template
19. lcd1232/gameview-music-deck. https://github.com/lcd1232/gameview-music-deck
20. Getting Started - Deckbrew. https://wiki.deckbrew.xyz/plugin-dev/getting-started
21. Tormak9970/Decky-QuickStart. https://github.com/Tormak9970/Decky-QuickStart
22. SteamDeckHomebrew/decky-frontend-lib. https://github.com/SteamDeckHomebrew/decky-frontend-lib
23. jessebofill/DeckWebBrowser. https://github.com/jessebofill/DeckWebBrowser
24. ytmusicapi - conda-forge. https://anaconda.org/conda-forge/ytmusicapi
25. ytmusicapi Documentation. https://ytmusicapi.readthedocs.io/
26. yt-dlp/yt-dlp. https://github.com/yt-dlp/yt-dlp
27. How to stream audio from a Youtube URL in Python. https://stackoverflow.com/questions/49354232/how-to-stream-audio-from-a-youtube-url-in-python-without-download
28. yt-dlp+mpv chained media playback. https://unix.stackexchange.com/questions/750166/yt-dlp-mpv-chained-media-playback
29. How to configure Virtual Surround on Steam Deck using Pipewire. https://www.reddit.com/r/SteamDeck/comments/18wn8de/how_to_configure_virtual_surround_on_steam_deck/
30. PipeWire support · Issue #8569 · mpv-player/mpv. https://github.com/mpv-player/mpv/issues/8569
31. [SOLVED] pipewire 1:1.4.3-1 audio underrun in mpv. https://bbs.archlinux.org/viewtopic.php?id=305749
32. decky-plugin-template/decky.pyi. https://github.com/SteamDeckHomebrew/decky-plugin-template/blob/main/decky.pyi
33. ytmusicapi/ytmusicapi - Packagist.org. https://packagist.org/packages/ytmusicapi/ytmusicapi
34. nick42d/youtui: TUI and API for YouTube Music. https://github.com/nick42d/youtui
35. Decky loader plug-in problem: r/SteamOS. https://www.reddit.com/r/SteamOS/comments/1sqtj0v/decky_loader_plugin_problem/
36. Releases · DeckThemes/SDH-AudioLoader. https://github.com/EMERALD0874/SDH-AudioLoader/releases
37. DeckThemes/SDH-AudioLoader. https://github.com/DeckThemes/SDH-AudioLoader
38. Home - DeckThemes Docs. https://docs.deckthemes.com/AudioLoader/
39. Decky Loader: How to Start Developing Plugins. https://magicpods.app/blog/post-11/
40. How to Enable Developer Mode on Steam Deck. https://www.youtube.com/watch?v=lY-ppo8VvPk
41. How to load and run games on Steam Deck. https://partner.steamgames.com/doc/steamdeck/loadgames
42. Submitting Plugins - Deckbrew. https://wiki.deckbrew.xyz/en/plugin-dev/submitting-plugins
43. Adventures in trying to build a Decky Plugin: r/SteamDeck. https://www.reddit.com/r/SteamDeck/comments/1tnmldx/adventures_in_trying_to_build_a_decky_plugin/
44. Devin Green artistro08 - GitHub. https://github.com/artistro08
45. ShadowApex/crankshaft-decky-plugin-template. https://github.com/ShadowApex/crankshaft-decky-plugin-template

---

> 📝 **Nota final**: Este documento está diseñado como guía técnica para desarrolladores que deseen crear un plugin nativo de YouTube Music para SteamOS mediante Decky Loader. Se recomienda seguir estrictamente las políticas de distribución y licenciamiento para garantizar compatibilidad y aprobación en la tienda oficial de la comunidad.