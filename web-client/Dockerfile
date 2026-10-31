FROM node:24-alpine
WORKDIR /app
COPY --chown=node:node dev-server.mjs index.html styles.css app.js api.js workspace.js workspace.css appearance.js Manrope-Variable.ttf Manrope-OFL.txt ./
COPY --chown=node:node assets ./assets
USER node
EXPOSE 5173
CMD ["node", "dev-server.mjs"]
