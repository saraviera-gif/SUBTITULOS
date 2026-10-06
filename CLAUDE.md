# Revisión de subtítulos quemados

`revisar.py` descarga el vídeo, transcribe el audio y hace OCR de los subtítulos.
Ejecutar siempre con el entorno del proyecto: `.venv/bin/python revisar.py "<enlace>" [--prueba MIN]`.
Deja todo en `trabajos/<vídeo>/`. La revisión la hace Claude Code (sin APIs de pago).

## Flujo cuando el usuario pega un enlace de Dropbox

1. Si no existe `.venv/`, ejecuta `./instalar.command` (o pide al usuario que haga doble clic en él).
2. Lanza el script **en segundo plano** (tarda: descarga de GB, transcripción, OCR) y redirige la salida a
   un log: `mkdir -p trabajos && .venv/bin/python revisar.py "<enlace>" [--prueba MIN] > trabajos/ultimo.log 2>&1`.
   Usa `--prueba 3` si el usuario pide una prueba. Ve informando de cada fase (líneas `▶` del log).
3. Cuando termine, abre `zona_preview.jpg` y comprueba que el recuadro rojo cubre los subtítulos; si no,
   repite con `--zona INICIO,FIN` (fracciones de la altura).
4. Haz la revisión (sección siguiente), escribe `fallos.json`, genera el informe con `--informe` y dile al
   usuario la ruta de `informe_revision.html` y del PDF, con un resumen de fallos por tipo.

El usuario no tiene por qué saber programar: explica en lenguaje sencillo y no le pidas que ejecute comandos.

## Cómo revisar (cuando el usuario pida «revisa los subtítulos de trabajos/X»)

1. Lee `trabajos/X/bloques/bloque_NNN.json` de uno en uno (40 subtítulos cada uno).
2. Para cada subtítulo compara `texto_ocr` con `audio_texto` (y `audio_antes`/`audio_despues`, por si la
   frase está desplazada) y aplica los criterios:
   - **a) ortografia**: tildes, mayúsculas, puntuación (¿ ¡ de apertura, comas evidentes), errores
     gramaticales, palabras mal escritas, nombres propios/marcas mal escritos.
   - **b) fidelidad**: el subtítulo no dice lo que se oye: palabras cambiadas, omitidas o añadidas que
     alteran el sentido o el nombre/cifra. NO marcar simplificaciones normales de subtitulado (quitar
     muletillas, «eh», repeticiones) ni errores evidentes de la transcripción automática.
   - **c) sincronia**: usar `desfase_entrada` (>0 = entra tarde) y `desfase_salida` (<0 = se va antes de
     terminar la frase). Como los tiempos tienen ±0,33 s de precisión, marcar solo si entra ≥0,9 s
     tarde o >1 s antes de la voz, si desaparece ≥0,9 s antes de que acabe la frase, o si dura <0,5 s
     en pantalla (ilegible). Las frases grandes animadas que resumen lo dicho son estilo, no fallo, salvo
     que se pierda información. Si `pista_parcial` es true o `coincidencia_audio` < 0,6, comprobar a mano
     con `audio_antes`/`audio_despues` antes de marcar.
   - Huecos: si entre dos subtítulos se oye una frase que no aparece en pantalla (mirar `audio_despues`
     y la transcripción), es **fidelidad** (omisión), salvo en tramos de grafismo a pantalla completa.
   - **Subtítulos de diseño** (`texto_grande: true`: frases grandes animadas, rótulos con el nombre,
     cifras que se animan como «3.000 → 3.001»): son intencionados. En ellos marca SOLO faltas de
     ortografía claras (tildes, palabras mal escritas). NUNCA omisiones, sincronía, resúmenes de lo dicho ni
     formato de números. Tampoco marques palabras que se oyen sin subtítulo justo antes/después de un texto
     grande. Si dudas de si algo es intencionado, añade `"dudoso": true` (sale descartado por defecto en el
     informe y el usuario puede recuperarlo).
3. **Antes de anotar un fallo de ortografía, abre la `captura` con Read** y confirma que el error está en la
   imagen y no es un fallo del OCR (EasyOCR suele perder tildes, ¿¡, confundir l/I, rn/m). Si el OCR leyó
   mal pero el subtítulo está bien, no es fallo. Si `texto_ocr` es basura por un fundido, ignóralo.
4. Escribe `trabajos/X/fallos.json`:
   ```json
   {"fallos": [
     {"id": 12, "tipo": "ortografia", "texto_actual": "texto tal como aparece en pantalla",
      "correccion": "texto corregido completo", "explicacion": "Falta la tilde en «está»."}
   ]}
   ```
   `tipo` ∈ `ortografia | fidelidad | sincronia`. Opcional: `"dudoso": true`. Un subtítulo con dos tipos de fallo → dos entradas.
   `texto_actual` debe ser el texto real de la captura (corregido si el OCR falló). Explicaciones breves.
   En vídeos largos, ve añadiendo fallos al archivo tras cada bloque para no perder trabajo.
5. Genera el informe: `.venv/bin/python revisar.py "<enlace>" [--prueba MIN] --informe`
   (crea `informe_revision.html` y `informe_revision.pdf`). En el HTML el usuario selecciona fallos y
   los descarta o los exporta a Word/PDF para el editor; el PDF automático excluye los `dudoso`.
