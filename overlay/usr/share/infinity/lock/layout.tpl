<?xml version="1.0" encoding="UTF-8"?>
<!-- Plantilla del diseño de la pantalla de bloqueo (gtklock). infinity-lock la rellena según la pantalla:
     @MARGEN_SUPERIOR@, @MARGEN_DERECHO@ y @MARGEN_FECHA@ son píxeles. No edites esos marcadores.
     Igual que la maqueta de referencia: la hora en dos líneas, gigante, a la derecha; la fecha
     pequeña debajo. Los "id" son los que gtklock espera: no los cambies. El aspecto está en style.css. -->
<interface>
	<object class="GtkBox" id="window-box">
		<property name="name">window-box</property>
		<property name="halign">fill</property>
		<property name="valign">fill</property>
		<property name="orientation">vertical</property>
		<property name="spacing">0</property>
		<child>
			<!-- La hora y la fecha: arriba a la derecha, como en la referencia -->
			<object class="GtkBox" id="info-box">
				<property name="name">info-box</property>
				<property name="halign">end</property>
				<property name="valign">start</property>
				<property name="margin-top">@MARGEN_SUPERIOR@</property>
				<property name="margin-end">@MARGEN_DERECHO@</property>
				<property name="orientation">vertical</property>
				<child>
					<object class="GtkBox" id="time-box">
						<property name="name">time-box</property>
						<property name="halign">end</property>
						<property name="orientation">vertical</property>
						<child>
							<object class="GtkLabel" id="clock-label">
								<property name="name">clock-label</property>
								<property name="halign">end</property>
								<property name="justify">right</property>
							</object>
						</child>
						<child>
							<object class="GtkLabel" id="date-label">
								<property name="name">date-label</property>
								<property name="halign">end</property>
								<property name="margin-top">@MARGEN_FECHA@</property>
							</object>
						</child>
					</object>
				</child>
			</object>
		</child>
		<child>
			<!-- La contraseña: aparece al tocar una tecla o el ratón, abajo al centro -->
			<object class="GtkRevealer" id="body-revealer">
				<property name="transition-type">crossfade</property>
				<property name="transition-duration">250</property>
				<property name="reveal-child">0</property>
				<property name="halign">center</property>
				<property name="valign">end</property>
				<property name="margin-bottom">90</property>
				<child>
					<object class="GtkGrid" id="body-grid">
						<property name="row-spacing">10</property>
						<property name="column-spacing">8</property>
						<property name="halign">center</property>
						<child>
							<object class="GtkLabel" id="input-label">
								<property name="name">input-label</property>
								<property name="label"></property>
								<property name="no-show-all">1</property>
							</object>
							<packing>
								<property name="left-attach">0</property>
								<property name="top-attach">0</property>
							</packing>
						</child>
						<child>
							<object class="GtkEntry" id="input-field">
								<property name="name">input-field</property>
								<property name="width-request">420</property>
								<property name="visibility">0</property>
								<property name="caps-lock-warning">0</property>
								<property name="input-purpose">password</property>
								<property name="placeholder-text">Contraseña  (Enter si no tienes)</property>
								<property name="halign">center</property>
								<signal name="activate" handler="window_pw_check"/>
							</object>
							<packing>
								<property name="left-attach">1</property>
								<property name="top-attach">0</property>
								<property name="width">2</property>
							</packing>
						</child>
						<child>
							<object class="GtkRevealer" id="message-revealer">
								<property name="transition-type">none</property>
								<property name="no-show-all">1</property>
								<child>
									<object class="GtkScrolledWindow" id="message-scrolled-window">
										<property name="hscrollbar-policy">never</property>
										<property name="max-content-height">256</property>
										<property name="propagate-natural-height">1</property>
										<child>
											<object class="GtkViewport">
												<child>
													<object class="GtkBox" id="message-box">
														<property name="orientation">vertical</property>
														<property name="homogeneous">1</property>
													</object>
												</child>
											</object>
										</child>
									</object>
								</child>
							</object>
							<packing>
								<property name="left-attach">1</property>
								<property name="top-attach">1</property>
								<property name="width">2</property>
							</packing>
						</child>
						<child>
							<object class="GtkBox">
								<property name="halign">center</property>
								<property name="spacing">8</property>
								<child>
									<object class="GtkLabel" id="warning-label">
										<property name="name">warning-label</property>
									</object>
								</child>
								<child>
									<object class="GtkLabel" id="error-label">
										<property name="name">error-label</property>
									</object>
								</child>
								<child>
									<object class="GtkButton" id="unlock-button">
										<property name="name">unlock-button</property>
										<property name="label">Desbloquear</property>
										<signal name="clicked" handler="window_pw_check"/>
									</object>
								</child>
							</object>
							<packing>
								<property name="left-attach">1</property>
								<property name="top-attach">2</property>
								<property name="width">2</property>
							</packing>
						</child>
					</object>
				</child>
			</object>
		</child>
	</object>
</interface>
