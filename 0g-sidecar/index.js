'use strict';

require('dotenv').config();
const { createApp } = require('./service');

const mode = process.env.ZG_MODE;
if (!mode) throw new Error('ZG_MODE must be explicitly set to mock or live');
if (!['mock', 'live'].includes(mode)) throw new Error('ZG_MODE must be explicitly set to mock or live');
if (mode === 'live' && !process.env.ZG_PRIVATE_KEY) {
  throw new Error('ZG_MODE=live requires ZG_PRIVATE_KEY');
}

const port = Number(process.env.ZG_SIDECAR_PORT || 5053);
createApp({ mode }).listen(port, '0.0.0.0', () => {
  console.log(`0G storage sidecar listening on ${port} (${mode})`);
});
