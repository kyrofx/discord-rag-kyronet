const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const rag_api = require('../../apis/rag-api.js');

module.exports = {
    data: new SlashCommandBuilder()
        .setName('ask')
        .setDescription('Ask a question about chat history')
        .addStringOption(option => option.setName('prompt')
                .setDescription('The question to ask')
                .setRequired(true)),
    async execute(interaction) {
        if (!await rag_api.healthCheck()) return await interaction.reply({ content: "API is currently unavailable. Please try again later.", ephemeral: true });

        await interaction.reply({ content: "Processing your question...", ephemeral: true});
        await processText(interaction);
    },
};

const processText = async (interaction) => {
    console.log('Processing text')
    const text = interaction.options.getString('prompt');
    const response = await rag_api.inference(text);
    if (!response) return await interaction.editReply({ content: "Error while processing the text. Please try again later.", ephemeral: true });

    await interaction.deleteReply();

    // Build embed response
    const embed = new EmbedBuilder()
        .setTitle('Answer')
        .setDescription(response.answer)
        .setColor(0x5865F2) // Discord blurple
        .setFooter({ text: `Asked by ${interaction.user.displayName}` })
        .setTimestamp();

    // Add sources if available
    if (response.sources && response.sources.length > 0) {
        let sourcesText = '';

        for (const src of response.sources.slice(0, 3)) { // Limit to 3 sources
            const sourceNum = src.source_number;
            const urls = src.urls || [];

            if (urls.length > 0) {
                // Link to first message in chunk
                sourcesText += `**[${sourceNum}]** [Jump to message](${urls[0]})\n`;
            } else {
                sourcesText += `**[${sourceNum}]** _(no link available)_\n`;
            }

            // Add snippet in a code block (truncated)
            const snippet = src.snippet ? src.snippet.substring(0, 100) : '';
            if (snippet) {
                sourcesText += `\`\`\`${snippet}${src.snippet && src.snippet.length > 100 ? '...' : ''}\`\`\`\n`;
            }
        }

        if (sourcesText) {
            embed.addFields({
                name: 'Sources',
                value: sourcesText,
                inline: false
            });
        }
    }

    await interaction.channel.send({
        content: `<@${interaction.user.id}> asked: __${text}__`,
        embeds: [embed]
    });
}
