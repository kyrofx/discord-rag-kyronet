const cron = require('node-cron');
const { MongoClient } = require('mongodb');
const { Client, GatewayIntentBits, ChannelType } = require('discord.js');
const { createClient } = require('redis');
const axios = require('axios');
const config = require('./config.js');

const MAX_FETCH_LIMIT = 100;
const QUEUE_KEY = 'discord_rag:ingest_queue';
const STATUS_KEY = 'discord_rag:bot:status';
const HEARTBEAT_KEY = 'discord_rag:bot:heartbeat';

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

// Redis client
let redisClient = null;
const startTime = Date.now();

/**
 * Initialize Redis connection
 */
async function initRedis() {
    try {
        redisClient = createClient({ url: config.redisUrl });
        redisClient.on('error', (err) => console.error('[Scheduler] Redis error:', err.message));
        await redisClient.connect();
        console.log('[Scheduler] Redis connected');
        return true;
    } catch (err) {
        console.error('[Scheduler] Redis connection failed:', err.message);
        return false;
    }
}

/**
 * Update bot status in Redis
 */
async function updateBotStatus() {
    if (!redisClient) return;

    try {
        const uptime = Math.floor((Date.now() - startTime) / 1000);

        await redisClient.hSet(STATUS_KEY, {
            status: 'online',
            guild_count: String(discordClient.guilds.cache.size),
            uptime_seconds: String(uptime),
            started_at: new Date(startTime).toISOString(),
            username: discordClient.user?.username || '',
            discriminator: discordClient.user?.discriminator || '',
            avatar_url: discordClient.user?.displayAvatarURL() || ''
        });

        await redisClient.set(HEARTBEAT_KEY, new Date().toISOString());
    } catch (err) {
        console.error('[Scheduler] Failed to update status:', err.message);
    }
}

/**
 * Cache guild metadata and channels in Redis
 */
async function cacheGuildData() {
    if (!redisClient) return;

    try {
        for (const [guildId, guild] of discordClient.guilds.cache) {
            // Cache guild metadata
            await redisClient.hSet(`discord_rag:guild:${guildId}:meta`, {
                name: guild.name,
                icon: guild.iconURL() || '',
                member_count: String(guild.memberCount)
            });

            // Cache text channels
            const textChannels = guild.channels.cache
                .filter(ch => ch.type === ChannelType.GuildText)
                .map(ch => ({
                    id: ch.id,
                    name: ch.name,
                    type: 'text',
                    category: ch.parent?.name || null
                }));

            await redisClient.set(
                `discord_rag:guild:${guildId}:channels`,
                JSON.stringify(textChannels)
            );
        }
        console.log(`[Scheduler] Cached data for ${discordClient.guilds.cache.size} guilds`);
    } catch (err) {
        console.error('[Scheduler] Failed to cache guild data:', err.message);
    }
}

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
async function ingestMessages(channelIdFilter = null) {
    console.log('[Scheduler] Starting message ingestion...');

    const channelIds = channelIdFilter
        ? [channelIdFilter]
        : config.channelIds;

    if (channelIds.length === 0) {
        console.log('[Scheduler] No channels configured for ingestion');
        return 0;
    }

    try {
        await mongoClient.connect();
        const db = mongoClient.db(config.mongodb.db);
        const collection = db.collection(config.mongodb.collection);

        let totalProcessed = 0;

        for (const channelId of channelIds) {
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

                // Update guild stats in Redis
                if (redisClient && channel.guild) {
                    const guildStatsKey = `discord_rag:guild:${channel.guild.id}:stats`;
                    const currentStats = await redisClient.hGetAll(guildStatsKey);
                    const totalMessages = parseInt(currentStats.total_messages || '0') + messagesProcessed;

                    await redisClient.hSet(guildStatsKey, {
                        total_messages: String(totalMessages),
                        last_indexed: new Date().toISOString(),
                        newest_message: new Date().toISOString()
                    });
                }
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
 * Process manual trigger from Redis queue
 */
async function processManualTrigger(jobData) {
    console.log(`[Scheduler] Processing manual trigger from ${jobData.triggered_by || 'admin'}`);

    const jobId = jobData.job_id;
    const channelId = jobData.channel_id || null;

    // Update job status
    if (redisClient && jobId) {
        await redisClient.hSet(`discord_rag:job:${jobId}`, {
            status: 'running',
            started_at: new Date().toISOString(),
            triggered_by: jobData.triggered_by || 'admin'
        });
    }

    try {
        const messagesIngested = await ingestMessages(channelId);

        if (messagesIngested > 0) {
            await triggerIndexing();
        }

        // Update job status
        if (redisClient && jobId) {
            await redisClient.hSet(`discord_rag:job:${jobId}`, {
                status: 'completed',
                completed_at: new Date().toISOString(),
                messages_ingested: String(messagesIngested)
            });
        }

        console.log(`[Scheduler] Manual trigger complete. ${messagesIngested} messages ingested.`);
    } catch (err) {
        console.error('[Scheduler] Manual trigger failed:', err.message);

        if (redisClient && jobId) {
            await redisClient.hSet(`discord_rag:job:${jobId}`, {
                status: 'failed',
                error: err.message,
                completed_at: new Date().toISOString()
            });
        }
    }
}

/**
 * Poll Redis queue for manual triggers
 */
async function pollQueue() {
    if (!redisClient) return;

    try {
        // BRPOP with 5 second timeout
        const result = await redisClient.brPop(QUEUE_KEY, 5);

        if (result) {
            try {
                const jobData = JSON.parse(result.element);
                await processManualTrigger(jobData);
            } catch (err) {
                console.error('[Scheduler] Failed to parse job data:', err.message);
            }
        }
    } catch (err) {
        if (err.message !== 'Connection is closed.') {
            console.error('[Scheduler] Queue poll error:', err.message);
        }
    }

    // Continue polling
    setImmediate(pollQueue);
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
discordClient.once('ready', async () => {
    console.log(`[Scheduler] Discord client ready as ${discordClient.user.tag}`);
    console.log(`[Scheduler] Monitoring channels: ${config.channelIds.join(', ')}`);
    console.log(`[Scheduler] Schedule: ${config.schedule.cronExpression}`);
    console.log(`[Scheduler] Quiet period: ${config.schedule.quietPeriodMinutes} minutes`);
    console.log(`[Scheduler] Backoff: ${config.schedule.backoffMinutes} minutes`);

    // Initialize Redis
    await initRedis();

    // Update status immediately
    await updateBotStatus();

    // Cache guild data
    await cacheGuildData();

    // Start heartbeat interval (every 30 seconds)
    setInterval(updateBotStatus, 30000);

    // Refresh guild cache every 5 minutes
    setInterval(cacheGuildData, 5 * 60 * 1000);

    // Start queue polling for manual triggers
    console.log('[Scheduler] Starting queue listener for manual triggers...');
    pollQueue();

    // Schedule the cron job
    cron.schedule(config.schedule.cronExpression, () => {
        console.log(`[Scheduler] Cron triggered at ${new Date().toISOString()}`);
        runScheduledJob();
    });

    console.log('[Scheduler] Cron job scheduled. Waiting for next run...');
});

// Handle graceful shutdown
async function shutdown() {
    console.log('[Scheduler] Shutting down...');

    if (redisClient) {
        await redisClient.hSet(STATUS_KEY, { status: 'offline' });
        await redisClient.quit();
    }

    await discordClient.destroy();
    process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

// Start
discordClient.login(config.token);
