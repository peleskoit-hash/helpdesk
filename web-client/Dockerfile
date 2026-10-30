FROM node:24-alpine
WORKDIR /app
COPY --chown=node:node dev-server.mjs index.html styles.css app.js api.js ./
USER node
EXPOSE 5173
CMD ["node", "dev-server.mjs"]
