const cron = require('node-cron');
const { MongoClient } = require('mongodb');
const { Client, GatewayIntentBits } = require('discord.js');
const axios = require('axios');
const config = require('./config.js');

const MAX_FETCH_LIMIT = 100;

// Discord client for fetching messages
const discordClient = new Client({
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent
    ]
});

// MongoDB client
const mongoClient = new MongoClient(config.mongodb.url);

/**
 * Check if there have been messages in the last N minutes
 */
async function hasRecentActivity(quietPeriodMinutes) {
    const cutoffTime = Date.now() - (quietPeriodMinutes * 60 * 1000);

    try {
        await mongoClient.connect();
        const db = mongoClient.db(config.mongodb.db);
        const collection = db.collection(config.mongodb.collection);

        const recentMessage = await collection.findOne(
            { timestamp: { $gte: cutoffTime } },
            { sort: { timestamp: -1 } }
        );

        return recentMessage !== null;
    } finally {
        await mongoClient.close();
    }
}

/**
 * Fetch and store new messages from Discord
 */
async function ingestMessages() {
    console.log('[Scheduler] Starting message ingestion...');

    try {
        await mongoClient.connect();
        const db = mongoClient.db(config.mongodb.db);
        const collection = db.collection(config.mongodb.collection);

        let totalProcessed = 0;

        for (const channelId of config.channelIds) {
            try {
                const channel = await discordClient.channels.fetch(channelId);
                let latestStoredMessageId = await getLatestStoredMessageId(collection, channelId);
                let messagesProcessed = 0;
                let messagesCollection;

                console.log(`[Scheduler] Ingesting from channel ${channelId}...`);

                do {
                    messagesCollection = await channel.messages.fetch({
                        limit: MAX_FETCH_LIMIT,
                        cache: false,
                        after: latestStoredMessageId
                    });

                    if (messagesCollection && messagesCollection.size > 0) {
                        const filteredMessages = Array.from(messagesCollection.values())
                            .filter(msg => msg.author.id !== discordClient.user.id);

                        const messages = filteredMessages.map(msg => ({
                            _id: msg.id,
                            content: msg.content,
                            timestamp: msg.createdTimestamp,
                            url: `https://discord.com/channels/${msg.guild.id}/${msg.channel.id}/${msg.id}`,
                            channel: { id: msg.channel.id },
                            author: { id: msg.author.id, username: msg.author.username },
                            guild: { id: msg.guild.id }
                        }));

                        if (messages.length > 0) {
                            const bulkOps = messages.map(message => ({
                                updateOne: {
                                    filter: { _id: message._id },
                                    update: { $set: message },
                                    upsert: true
                                }
                            }));
                            await collection.bulkWrite(bulkOps);

                            latestStoredMessageId = messages.reduce(
                                (acc, msg) => msg.timestamp > acc.timestamp ? msg : acc
                            )._id;
                            messagesProcessed += messages.length;
                        }
                    }
                } while (messagesCollection && messagesCollection.size === MAX_FETCH_LIMIT);

                console.log(`[Scheduler] Channel ${channelId}: ${messagesProcessed} new messages`);
                totalProcessed += messagesProcessed;
            } catch (err) {
                console.error(`[Scheduler] Error ingesting channel ${channelId}:`, err.message);
            }
        }

        console.log(`[Scheduler] Ingestion complete. Total: ${totalProcessed} messages`);
        return totalProcessed;
    } finally {
        await mongoClient.close();
    }
}

async function getLatestStoredMessageId(collection, channelId) {
    const latestMessage = await collection
        .find({ 'channel.id': channelId })
        .sort({ timestamp: -1 })
        .limit(1)
        .toArray();
    return latestMessage.length ? latestMessage[0]._id : '0';
}

/**
 * Trigger the indexing pipeline via API
 */
async function triggerIndexing() {
    console.log('[Scheduler] Triggering indexing pipeline...');

    try {
        const headers = config.apiKey
            ? { Authorization: `Bearer ${config.apiKey}` }
            : {};

        // Get guild IDs from the channels we monitor
        const guildIds = new Set();
        for (const channelId of config.channelIds) {
            try {
                const channel = await discordClient.channels.fetch(channelId);
                if (channel.guild) {
                    guildIds.add(channel.guild.id);
                }
            } catch (err) {
                console.error(`[Scheduler] Could not fetch channel ${channelId}:`, err.message);
            }
        }

        // Trigger indexing for each guild
        for (const guildId of guildIds) {
            try {
                const response = await axios.post(
                    `${config.apiBaseUrl}/v1/guilds/${guildId}/index`,
                    {},
                    { headers, timeout: 300000 } // 5 min timeout for indexing
                );
                console.log(`[Scheduler] Indexing triggered for guild ${guildId}:`, response.data);
            } catch (err) {
                console.error(`[Scheduler] Indexing failed for guild ${guildId}:`, err.message);
            }
        }

        console.log('[Scheduler] Indexing complete');
    } catch (err) {
        console.error('[Scheduler] Indexing error:', err.message);
    }
}

/**
 * Main scheduled job with quiet period check and backoff
 */
async function runScheduledJob(retriesRemaining = config.schedule.maxRetries) {
    const { quietPeriodMinutes, backoffMinutes } = config.schedule;

    console.log(`[Scheduler] Checking for activity in last ${quietPeriodMinutes} minutes...`);

    try {
        const hasActivity = await hasRecentActivity(quietPeriodMinutes);

        if (hasActivity) {
            if (retriesRemaining > 0) {
                console.log(`[Scheduler] Activity detected. Backing off ${backoffMinutes} minutes... (${retriesRemaining} retries left)`);
                setTimeout(() => runScheduledJob(retriesRemaining - 1), backoffMinutes * 60 * 1000);
            } else {
                console.log('[Scheduler] Max retries reached. Skipping this run.');
            }
            return;
        }

        console.log('[Scheduler] No recent activity. Starting ingestion...');

        // Run ingestion
        const messagesIngested = await ingestMessages();

        // Only run indexing if we got new messages
        if (messagesIngested > 0) {
            await triggerIndexing();
        } else {
            console.log('[Scheduler] No new messages to index.');
        }

        console.log('[Scheduler] Scheduled run complete.');
    } catch (err) {
        console.error('[Scheduler] Job failed:', err.message);
    }
}

// Initialize
discordClient.once('ready', () => {
    console.log(`[Scheduler] Discord client ready as ${discordClient.user.tag}`);
    console.log(`[Scheduler] Monitoring channels: ${config.channelIds.join(', ')}`);
    console.log(`[Scheduler] Schedule: ${config.schedule.cronExpression}`);
    console.log(`[Scheduler] Quiet period: ${config.schedule.quietPeriodMinutes} minutes`);
    console.log(`[Scheduler] Backoff: ${config.schedule.backoffMinutes} minutes`);

    // Schedule the job
    cron.schedule(config.schedule.cronExpression, () => {
        console.log(`[Scheduler] Cron triggered at ${new Date().toISOString()}`);
        runScheduledJob();
    });

    console.log('[Scheduler] Cron job scheduled. Waiting for next run...');
});

// Handle graceful shutdown
process.on('SIGINT', async () => {
    console.log('[Scheduler] Shutting down...');
    await discordClient.destroy();
    process.exit(0);
});

process.on('SIGTERM', async () => {
    console.log('[Scheduler] Shutting down...');
    await discordClient.destroy();
    process.exit(0);
});

// Start
discordClient.login(config.token);
