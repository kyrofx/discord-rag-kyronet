module.exports = {
    // Discord
    token: process.env.DISCORD_BOT_TOKEN,
    channelIds: (process.env.DISCORD_CHANNEL_IDS || '').split(',').filter(Boolean),

    // MongoDB
    mongodb: {
        url: process.env.MONGODB_URL,
        db: process.env.MONGODB_DB,
        collection: process.env.MONGODB_COLLECTION
    },

    // API
    apiBaseUrl: process.env.RAG_API_BASE_URL || 'http://discord_rag_api:8000',
    apiKey: process.env.API_KEY || '',

    // Schedule settings
    schedule: {
        // Cron expression: 3 AM daily
        cronExpression: process.env.SCHEDULE_CRON || '0 3 * * *',
        // Minutes of quiet time required before ingestion
        quietPeriodMinutes: parseInt(process.env.QUIET_PERIOD_MINUTES || '15', 10),
        // Minutes to back off if activity detected
        backoffMinutes: parseInt(process.env.BACKOFF_MINUTES || '10', 10),
        // Maximum retry attempts
        maxRetries: parseInt(process.env.MAX_RETRIES || '6', 10)
    }
};
