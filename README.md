# TIDAL Connect para Linux

App de escritorio para Omarchy (Linux) que funciona como la app de TIDAL de Mac o
Windows cuando eliges un altavoz con **TIDAL Connect**: el ordenador solo da las
órdenes y es el **equipo de música quien descarga la música de TIDAL**, en la máxima
calidad que admita (probado con un Eversolo DMP-A6: FLAC 24 bits / 48 kHz).

El ordenador no reproduce audio en ningún momento.

## Uso

- Ábrela desde el lanzador de aplicaciones: **TIDAL Connect**.
- La primera vez pide autorizar la cuenta en la web oficial de TIDAL (la contraseña
  no pasa por la app).
- Elige el altavoz con el botón de la barra inferior (se detectan solos en la red).

Atajos: `Espacio` play/pausa · `Ctrl+F` o `/` buscar · `Ctrl+←/→` canción
anterior/siguiente · `Alt+←/→` atrás/adelante · doble clic en una canción para
reproducir desde ahí · clic derecho para el menú.

## Este ordenador

Además de los altavoces TIDAL Connect, en el selector de altavoz está **Este ordenador**:
la música se reproduce aquí (con mpv), por la salida que elijas (altavoces, cascos,
Bluetooth o el DMP-A6 por USB). El audio llega de TIDAL en FLAC (24 bits si existe) y
se guarda en una caché temporal (`~/.cache/tidal-connect-linux/audio`, 12 canciones como
mucho) para poder saltar a cualquier punto. Al cambiar de altavoz mientras suena, la
música sigue en el nuevo desde el mismo punto (TIDAL solo deja reproducir en un sitio a
la vez por cuenta).

## TIDAL DJ (app aparte)

Lanzador **TIDAL DJ**: una cabina de DJ que **mezcla en este ordenador**. Comparte el
motor con la app principal (cuenta, biblioteca, cerebro del DJ y sesiones).

- **Cargar sesión:** usa tus sesiones (o un set rápido con tus favoritas); el DJ elige y
  ordena las canciones, y aquí se mezclan de verdad.
- **Análisis de cada canción:** golpes, primer tiempo de cada compás (el tempo de TIDAL
  sirve de pista), dónde empieza y acaba la música, forma de onda.
- **Mezcla automática:** la siguiente entra en un comienzo de compás, al mismo tempo
  (cambio de velocidad, hasta ±10 %; medio/doble tiempo si cuadra mejor), con un fundido
  de 4, 8 o 16 compases e intercambio de graves. Después vuelve poco a poco a su tempo
  (o lo mantiene, a elegir).
- **En directo:** crossfader, filtro por plato (graves/agudos), "Mezclar ya" (mezcla
  completa en el próximo compás, con o sin mezcla automática), "Saltar" (fundido corto
  ahora mismo), volumen y clic en la forma de onda para saltar.
- **Elegir la siguiente:** pestaña **Buscar en TIDAL** (todo el catálogo: canciones,
  artistas con su radio, álbumes y playlists) y pestaña **Encajan con la que suena**
  (radio de la canción ordenada por tono y tempo). Cada canción indica si encaja con la
  que suena. «Siguiente» la pone a sonar después (sustituye a la que iba a entrar);
  «+» la añade al final. En la lista se puede quitar, poner como siguiente o arrastrar
  para cambiar el orden. **Sesión libre:** sin lista, eligiendo cada canción.
- **Efectos** (botones o teclas 1 a 8; pulsar = se queda puesto, mantener = solo
  mientras aprietas): eco al compás, reverb, flanger, radio, lo-fi, corte rítmico,
  repetir (bucle corto al compás; al soltar, la canción sigue donde iba) y freno de
  disco (si la siguiente está lista, entra de golpe). Se aplican a la mezcla o a un
  plato, con golpes (¼ a 2) e intensidad.
- **Sonidos** (teclas Q a U), sintetizados por la propia app: bocina, sirena, láser,
  impacto (en el próximo tiempo), subidón y redoble de 2 compases (desde el próximo
  compás) y rascado hecho con la canción que suena.
- **Pre-escucha:** en **Salidas** se eligen dos aparatos: los altavoces (la mezcla) y
  los auriculares. El botón **Pre-escucha** de cada plato lo manda a los auriculares;
  si ese plato no está sonando, solo lo oyes tú y puedes moverte por su forma de onda.
  Un control decide qué oyes en los auriculares (pre-escucha, mezcla o las dos). Tienen
  que ser aparatos distintos (en muchos portátiles los altavoces y la toma de
  auriculares son el mismo). Si los auriculares van lentos, nunca frenan los altavoces.
- **Guardar como playlist:** la sesión (lo que ha sonado, los platos y lo que viene)
  se guarda como playlist privada de TIDAL.
- El motor de mezcla (`tidalconnect/djengine`) es un proceso aparte: decodifica con
  ffmpeg, mezcla con numpy/scipy y saca el sonido por PipeWire (`pw-cat`).
- Al empezar a mezclar se pausa la otra reproducción (un sitio a la vez por cuenta).
- Mezclar música de TIDAL fuera de sus apps de DJ asociadas no es un uso aprobado por
  TIDAL; pensado para uso personal.

## DJ

En la barra lateral, **DJ** (o el botón **Set DJ** de un álbum, artista, playlist o mix)
crea un set ordenado como lo haría un DJ:

- **Tempo:** cambia poco a poco (entiende el medio y el doble tiempo, 70 ≈ 140 BPM).
- **Tonalidad:** las canciones seguidas encajan (rueda Camelot: mismo tono, vecinos o
  relativo).
- **Estilo:** se mantiene, usando los artistas similares de TIDAL (se guardan en caché).
- **Energía:** sigue la forma elegida (subida, montaña, constante o bajada). TIDAL no da
  la energía, así que se estima con el tempo y el volumen de cada canción.
- **Fuentes cortas:** si la fuente no llega a la duración pedida, se completa con
  música parecida (radios de sus artistas), tendiendo puentes entre tus canciones, que
  entran siempre.

**Por estilos (toda la biblioteca de TIDAL):** el set se monta por **tramos**, cada uno
con uno o varios estilos (por ejemplo *Blues → Disco y Funk → Rock*). La música de cada
estilo sale de las playlists editoriales de TIDAL con ese nombre (sin las de Dolby Atmos,
que no traen tonalidad; caché de 7 días). Entre tramos, el DJ tiende un puente con
canciones que conectan ambos estilos, y la forma de la energía se aplica a todo el set.
Opción: dar preferencia a tus favoritas de esos estilos.

**Mis sesiones:** la estructura de un set (tramos y estilos, o fuente, duración, forma y
opciones) se puede guardar sin las canciones. Al pincharla se crea un set nuevo con esa
receta y se evitan las canciones de las últimas veces. Se guardan en
`~/.config/tidal-connect-linux/dj_sessions.json`.

La **cabina** muestra la canción actual y la siguiente con su tempo y tonalidad, si
encajan y por qué, una cuenta atrás, la curva de energía del set y lo que viene.
"Otra siguiente" propone alternativas que encajan y "Regenerar desde aquí" rehace el
resto del set. En **A continuación** cada canción se puede **cambiar** (por otra que
encaje en ese hueco o cualquiera de TIDAL), **quitar** o **meter otra detrás**.
**Guardar como playlist** guarda el set completo, en su orden, como playlist privada
de TIDAL.

Límite: el altavoz reproduce una canción cada vez, así que no hay fundidos ni mezcla
entre canciones. Unas 2 de cada 3 canciones de TIDAL tienen tempo y tonalidad.

## Instalación

```bash
./install.sh
```

Crea el entorno de Python, instala el servicio de usuario `tidal-connect.service`
(arranca al iniciar sesión), el icono y la entrada del lanzador. No necesita `sudo`.

## Cómo está hecho

```
tidalconnect/          motor local (Python, solo escucha en 127.0.0.1:47900)
  tidal_service.py     sesión de TIDAL (inicio de sesión por enlace, renovación)
  library.py           inicio, búsqueda, álbumes, artistas, playlists, colección
  connect_client.py    WebSocket TLS con el receptor TIDAL Connect
  player.py            cola + control del receptor (play, pausa, cola, volumen...)
  dj.py                DJ automático: tonalidad, tempo, estilo y curva de energía
  streams.py           audio de TIDAL (FLAC) para este ordenador + caché temporal
  local_output.py      reproducción en este ordenador con mpv
  djapp.py             controlador de la app TIDAL DJ (cola, preparar platos)
  djengine/            motor de mezcla (proceso aparte): análisis, mezcla en vivo,
                       efectos y sonidos (fx.py) y pre-escucha
web/dj/                interfaz de TIDAL DJ
bin/tidal-dj           lanzador de TIDAL DJ
  discovery.py         descubre receptores en la red (mDNS _tidalconnect._tcp)
  server.py            API JSON + eventos en vivo para la interfaz
web/                   interfaz (HTML/CSS/JS sin dependencias), estilo TIDAL
bin/tidal-connect      lanzador: arranca el motor y abre la ventana
prototype/             pruebas iniciales del protocolo
```

- **Datos de TIDAL:** biblioteca [tidalapi](https://github.com/tamland/python-tidal).
- **Protocolo TIDAL Connect:** no está documentado. El formato de los mensajes sale
  del receptor libre [tidal-connect-ng](https://github.com/resurekto/tidal-connect-ng)
  y se ha comprobado contra el DMP-A6. El mando abre una sesión (`startSession`),
  carga una cola local (`loadLocalQueue`) con un permiso de TIDAL que el receptor
  canjea por el suyo, y responde cuando el receptor pide canciones
  (`requestQueueItems`).

## Datos guardados

En `~/.config/tidal-connect-linux/` (permisos 700/600):
`session.json` (permiso de TIDAL), `queue.json` (cola actual), `settings.json`.

## Depuración

```bash
systemctl --user status tidal-connect
journalctl --user -u tidal-connect -f
```

Con `TIDAL_CONNECT_DEBUG=1` el registro muestra los mensajes con el receptor.

## Pendiente

- Teclas multimedia del teclado (MPRIS).
- Crear y editar playlists.
- Vídeos y Dolby Atmos.
