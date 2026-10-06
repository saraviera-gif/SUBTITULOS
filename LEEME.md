# Revisor de subtítulos quemados

Revisa los subtítulos que van "pegados" en la imagen de un vídeo de Dropbox y genera un informe con los
fallos de **ortografía**, **fidelidad al audio** y **sincronía**. Desde el informe eliges los fallos que
quieres y los descargas en **Word** o **PDF** para pasárselos al editor.

Puedes ver un ejemplo abriendo `ejemplo_informe.html`.

## Qué necesitas

- Un **Mac con chip Apple** (M1, M2, M3 o M4).
- **Claude Code** instalado (la app de escritorio de Claude, pestaña *Code*).
- **Google Chrome** (solo para el PDF automático).
- Unos **15 GB libres** para un vídeo largo, más ~5 GB la primera vez (modelos de transcripción y OCR).
- Conexión a internet.

## Instalación (una sola vez)

1. Descomprime la carpeta donde quieras (por ejemplo, en el Escritorio).
2. Haz **clic derecho → Abrir** sobre `instalar.command` y confirma *Abrir*.
   (La primera vez macOS avisa de que viene de internet; con clic derecho → Abrir te deja ejecutarlo.)
3. Espera a que salga **✅ Instalación terminada** (unos minutos). Ya puedes cerrar la ventana.

## Revisar un vídeo

1. Abre **Claude Code** y elige esta carpeta como carpeta de trabajo.
2. Escríbele algo así:

   > Revisa los subtítulos de este vídeo: https://www.dropbox.com/scl/fi/…

   Si es la primera vez con un vídeo nuevo, puedes pedir antes una prueba corta:

   > Haz una prueba con los 3 primeros minutos de este vídeo: https://www.dropbox.com/…

3. Claude descarga el vídeo, lo transcribe, lee los subtítulos, los revisa uno a uno (mirando las capturas
   para no confundir errores de lectura con faltas reales) y te dice dónde está el informe.
   En un vídeo de ~45 min el proceso automático tarda unos 20–30 min más la descarga, y la revisión
   de Claude un rato más.
4. El enlace de Dropbox tiene que ser **público** y apuntar al **archivo de vídeo** (no a una carpeta).

## Usar el informe

El informe queda en `trabajos/<nombre del vídeo>/informe_revision.html`. Ábrelo con Chrome.

- Arriba está el resumen de fallos por tipo y el **reproductor** del vídeo (se carga desde Dropbox).
- Haz clic en el **minuto** de un fallo para saltar a ese momento.
- **Selecciona** los fallos que quieras: clic en la fila o en su casilla (*Mayús+clic* para un rango,
  la casilla de la cabecera para todos).
- En la barra de abajo elige qué hacer con ellos:
  - **Descargar Word para el editor**: crea un .docx con minuto, captura, texto, corrección y explicación.
  - **PDF para el editor**: lo mismo en PDF.
  - **Descartar** / **Restaurar**: para quitar lo que no sea un fallo de verdad.
- Puedes **corregir** la propuesta o la explicación haciendo clic en el texto.
- Tu selección se guarda en ese navegador: si cierras y vuelves a abrir el informe, sigue igual.

También se genera `informe_revision.pdf` con todos los fallos, listo para mandar por WhatsApp o email.

## Qué se considera fallo

- **Ortografía**: tildes, mayúsculas, signos ¿ ¡, palabras mal escritas.
- **Fidelidad**: el subtítulo no dice lo que se oye (palabras cambiadas, omitidas o repetidas).
- **Sincronía**: el subtítulo entra o sale claramente desfasado (≈1 s o más) o dura tan poco que no se lee.
- Los **textos grandes animados** (rótulos, frases de diseño, cifras que se animan) se consideran
  intencionados: en ellos solo se marcan faltas de ortografía claras.

Las normas completas que sigue Claude están en `CLAUDE.md`; si queréis cambiar algún criterio, se puede
pedir a Claude que lo actualice.

## Si algo falla

- Si se corta a mitad (internet, se apaga el Mac…), vuelve a pedirlo igual: retoma donde se quedó.
- Si los subtítulos están en otra zona de la imagen y no los detecta bien, díselo a Claude: puede fijar
  la franja a mano (`--zona`).
- Los vídeos y archivos intermedios ocupan mucho: cuando ya no los necesites, borra la carpeta del vídeo
  dentro de `trabajos/` (el informe HTML y el PDF puedes guardarlos aparte).
