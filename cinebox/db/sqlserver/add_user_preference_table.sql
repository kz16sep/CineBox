-- Add UserPreference table for onboarding preferences
-- Schema for table [cine].[UserPreference]
IF OBJECT_ID('[cine].[UserPreference]', 'U') IS NOT NULL DROP TABLE [cine].[UserPreference];
GO
CREATE TABLE [cine].[UserPreference] (
    [prefId] bigint IDENTITY(1,1) NOT NULL,
    [userId] bigint NOT NULL,
    [preferenceType] nvarchar(20) NOT NULL, -- 'genre', 'actor', 'director'
    [preferenceId] bigint NOT NULL,
    [createdAt] datetime2 NOT NULL DEFAULT (sysutcdatetime())
);
GO

-- Add hasCompletedOnboarding column to User table if not exists
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('[cine].[User]') AND name = 'hasCompletedOnboarding')
BEGIN
    ALTER TABLE [cine].[User] ADD [hasCompletedOnboarding] bit NOT NULL DEFAULT (0);
END
GO

-- Create indexes for better performance
CREATE INDEX IX_UserPreference_UserId ON [cine].[UserPreference] ([userId]);
CREATE INDEX IX_UserPreference_Type ON [cine].[UserPreference] ([preferenceType]);
GO
