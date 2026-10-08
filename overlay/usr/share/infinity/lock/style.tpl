/* Plantilla de estilo de la pantalla de bloqueo. infinity-lock sustituye @TAMANO_HORA@ y @TAMANO_FECHA@
 * según la resolución. Pantalla de bloqueo de Infinity OS: igual que la maqueta de referencia.
 * La hora usa la fuente "Infinity Reloj" (Inter comprimida en horizontal, ver branding/make_fonts.py).
 * (gtklock usa GTK3, así que esto es CSS de GTK3.) */

* {
  font-family: "Inter", "Noto Sans", sans-serif;
  color: #ffffff;
}

/* ---- la hora: dos líneas gigantes y estrechas, blanco translúcido ---- */
window #clock-label,
window.focused:not(.hidden) #clock-label {
  font-family: "Infinity Reloj";
  font-size: @TAMANO_HORA@px;
  color: rgba(255, 255, 255, 0.80);
  text-shadow: 0 4px 22px rgba(10, 14, 40, 0.18);
}

/* ---- la fecha: pequeña, debajo de los minutos ---- */
window #date-label,
window.focused:not(.hidden) #date-label {
  font-size: @TAMANO_FECHA@px;
  font-weight: 400;
  color: rgba(255, 255, 255, 0.92);
  text-shadow: 0 1px 8px rgba(10, 14, 40, 0.30);
}

/* ---- contraseña: cápsula de cristal, aparece al tocar una tecla ---- */
window #input-field {
  min-height: 58px;
  padding: 0 26px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.34);
  background-color: rgba(20, 24, 50, 0.38);
  background-image: linear-gradient(180deg, rgba(255, 255, 255, 0.20), rgba(255, 255, 255, 0.03));
  font-size: 22px;
  caret-color: #ffffff;
  color: #ffffff;
}
window #input-field:focus {
  border-color: rgba(255, 255, 255, 0.60);
}

window #unlock-button {
  min-height: 46px;
  padding: 0 34px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.30);
  background-image: linear-gradient(180deg, rgba(255, 255, 255, 0.26), rgba(255, 255, 255, 0.06));
  background-color: rgba(20, 24, 50, 0.30);
  font-size: 18px;
  text-shadow: none;
}

window #error-label  { color: #ffb3c0; font-size: 18px; }
window #warning-label { color: #ffe3a1; font-size: 18px; }
