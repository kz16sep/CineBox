-- Fixed SQL script to import 100 movies
-- Run this in SQL Server Management Studio

USE CineBoxDB;
GO

-- Clear existing movies (except movieId = 1)
DELETE FROM cine.Movie WHERE movieId > 1;
GO

-- Enable IDENTITY_INSERT for Movie table
SET IDENTITY_INSERT cine.Movie ON;
GO

-- Insert 100 movies with explicit movieId values
INSERT INTO cine.Movie (movieId, title, releaseYear, overview, country, posterUrl, backdropUrl, viewCount, director, cast, durationMin, imdbRating, trailerUrl)
VALUES 
(2, 'Jumanji (1995)', 1995, 'When two kids find and play a magical board game, they release a man trapped in it for decades.', 'USA', 'https://via.placeholder.com/300x450?text=Jumanji', 'https://via.placeholder.com/1920x1080?text=Jumanji', 1500, 'Joe Johnston', 'Robin Williams, Kirsten Dunst', 104, 7.0, NULL),
(3, 'Grumpier Old Men (1995)', 1995, 'A family wedding reignites the ancient feud between next-door neighbors and fishing buddies.', 'USA', 'https://via.placeholder.com/300x450?text=Grumpier', 'https://via.placeholder.com/1920x1080?text=Grumpier', 800, 'Howard Deutch', 'Walter Matthau, Jack Lemmon', 101, 6.6, NULL),
(4, 'Waiting to Exhale (1995)', 1995, 'Four very different African-American women and their relationships with unfaithful men.', 'USA', 'https://via.placeholder.com/300x450?text=Waiting', 'https://via.placeholder.com/1920x1080?text=Waiting', 1200, 'Forest Whitaker', 'Whitney Houston, Angela Bassett', 127, 6.0, NULL),
(5, 'Father of the Bride Part II (1995)', 1995, 'George Banks receives the news that his daughter is pregnant and his wife is expecting too.', 'USA', 'https://via.placeholder.com/300x450?text=Father2', 'https://via.placeholder.com/1920x1080?text=Father2', 900, 'Charles Shyer', 'Steve Martin, Diane Keaton', 106, 6.1, NULL),
(6, 'Heat (1995)', 1995, 'A group of professional bank robbers start to feel the heat from police when they unknowingly leave a clue.', 'USA', 'https://via.placeholder.com/300x450?text=Heat', 'https://via.placeholder.com/1920x1080?text=Heat', 2500, 'Michael Mann', 'Al Pacino, Robert De Niro', 170, 8.2, NULL),
(7, 'Sabrina (1995)', 1995, 'An ugly duckling having undergone a remarkable change, still harbors feelings for her crush.', 'USA', 'https://via.placeholder.com/300x450?text=Sabrina', 'https://via.placeholder.com/1920x1080?text=Sabrina', 1100, 'Sydney Pollack', 'Harrison Ford, Julia Ormond', 127, 6.3, NULL),
(8, 'Tom and Huck (1995)', 1995, 'Tom Sawyer and his pal Huckleberry Finn have great adventures on the Mississippi River.', 'USA', 'https://via.placeholder.com/300x450?text=TomHuck', 'https://via.placeholder.com/1920x1080?text=TomHuck', 700, 'Peter Hewitt', 'Jonathan Taylor Thomas, Brad Renfro', 97, 5.4, NULL),
(9, 'Sudden Death (1995)', 1995, 'On a cold Christmas Eve in Pittsburgh, a fireman must save his daughter from terrorists.', 'USA', 'https://via.placeholder.com/300x450?text=Sudden', 'https://via.placeholder.com/1920x1080?text=Sudden', 600, 'Peter Hyams', 'Jean-Claude Van Damme, Powers Boothe', 110, 5.7, NULL),
(10, 'GoldenEye (1995)', 1995, 'James Bond teams up with the lone survivor of a destroyed Russian research center.', 'UK', 'https://via.placeholder.com/300x450?text=GoldenEye', 'https://via.placeholder.com/1920x1080?text=GoldenEye', 3000, 'Martin Campbell', 'Pierce Brosnan, Sean Bean', 130, 7.2, NULL),
(11, 'American President, The (1995)', 1995, 'A widowed U.S. president running for re-election and an environmental lobbyist fall in love.', 'USA', 'https://via.placeholder.com/300x450?text=American', 'https://via.placeholder.com/1920x1080?text=American', 1800, 'Rob Reiner', 'Michael Douglas, Annette Bening', 114, 6.8, NULL),
(12, 'Dracula: Dead and Loving It (1995)', 1995, 'Mel Brooks parody of the classic vampire story.', 'USA', 'https://via.placeholder.com/300x450?text=Dracula', 'https://via.placeholder.com/1920x1080?text=Dracula', 500, 'Mel Brooks', 'Leslie Nielsen, Peter MacNicol', 88, 5.4, NULL),
(13, 'Balto (1995)', 1995, 'An outcast half-wolf risks his life to prevent a deadly epidemic from ravaging Nome, Alaska.', 'USA', 'https://via.placeholder.com/300x450?text=Balto', 'https://via.placeholder.com/1920x1080?text=Balto', 800, 'Simon Wells', 'Kevin Bacon, Bob Hoskins', 78, 7.1, NULL),
(14, 'Nixon (1995)', 1995, 'A biographical story of former U.S. President Richard Milhous Nixon.', 'USA', 'https://via.placeholder.com/300x450?text=Nixon', 'https://via.placeholder.com/1920x1080?text=Nixon', 1200, 'Oliver Stone', 'Anthony Hopkins, Joan Allen', 192, 7.1, NULL),
(15, 'Cutthroat Island (1995)', 1995, 'A female pirate and her companion race against their rivals to find a hidden island.', 'USA', 'https://via.placeholder.com/300x450?text=Cutthroat', 'https://via.placeholder.com/1920x1080?text=Cutthroat', 400, 'Renny Harlin', 'Geena Davis, Matthew Modine', 119, 5.7, NULL),
(16, 'Casino (1995)', 1995, 'A tale of greed, deception, money, power, and murder between two best friends.', 'USA', 'https://via.placeholder.com/300x450?text=Casino', 'https://via.placeholder.com/1920x1080?text=Casino', 2800, 'Martin Scorsese', 'Robert De Niro, Sharon Stone', 178, 8.2, NULL),
(17, 'Sense and Sensibility (1995)', 1995, 'Rich Mr. Dashwood dies, leaving his second wife and her three daughters poor.', 'UK', 'https://via.placeholder.com/300x450?text=Sense', 'https://via.placeholder.com/1920x1080?text=Sense', 1500, 'Ang Lee', 'Emma Thompson, Kate Winslet', 136, 7.7, NULL),
(18, 'Four Rooms (1995)', 1995, 'Four interlocking tales that take place in a fading hotel on New Year''s Eve.', 'USA', 'https://via.placeholder.com/300x450?text=Four', 'https://via.placeholder.com/1920x1080?text=Four', 600, 'Allison Anders', 'Tim Roth, Antonio Banderas', 98, 6.7, NULL),
(19, 'Ace Ventura: When Nature Calls (1995)', 1995, 'Ace Ventura, Pet Detective, returns from a spiritual quest to investigate the disappearance of a rare white bat.', 'USA', 'https://via.placeholder.com/300x450?text=Ace', 'https://via.placeholder.com/1920x1080?text=Ace', 2000, 'Steve Oedekerk', 'Jim Carrey, Ian McNeice', 90, 6.4, NULL),
(20, 'Powder (1995)', 1995, 'A bald albino boy with paranormal powers faces prejudice in a small town.', 'USA', 'https://via.placeholder.com/300x450?text=Powder', 'https://via.placeholder.com/1920x1080?text=Powder', 800, 'Victor Salva', 'Mary Steenburgen, Sean Patrick Flanery', 111, 6.6, NULL),
(21, 'Othello (1995)', 1995, 'The Moorish General Othello is manipulated into thinking that his new wife Desdemona has been carrying on an affair.', 'UK', 'https://via.placeholder.com/300x450?text=Othello', 'https://via.placeholder.com/1920x1080?text=Othello', 300, 'Oliver Parker', 'Laurence Fishburne, Irene Jacob', 123, 6.8, NULL),
(22, 'Now and Then (1995)', 1995, 'Four 12-year-old girls grow up together during an eventful summer in 1970.', 'USA', 'https://via.placeholder.com/300x450?text=Now', 'https://via.placeholder.com/1920x1080?text=Now', 700, 'Lesli Linka Glatter', 'Christina Ricci, Rosie O''Donnell', 96, 6.8, NULL),
(23, 'Persuasion (1995)', 1995, 'Anne Elliot, a young Englishwoman of 27 years, whose family is moving to lower their expenses.', 'UK', 'https://via.placeholder.com/300x450?text=Persuasion', 'https://via.placeholder.com/1920x1080?text=Persuasion', 500, 'Roger Michell', 'Amanda Root, Ciarán Hinds', 107, 7.7, NULL),
(24, 'City of Lost Children, The (1995)', 1995, 'A scientist in a surrealist society kidnaps children to steal their dreams.', 'France', 'https://via.placeholder.com/300x450?text=City', 'https://via.placeholder.com/1920x1080?text=City', 400, 'Marc Caro', 'Ron Perlman, Daniel Emilfork', 112, 7.7, NULL),
(25, 'Shanghai Triad (1995)', 1995, 'A young boy from the countryside arrives in Shanghai in the 1930s and is introduced to organized crime.', 'China', 'https://via.placeholder.com/300x450?text=Shanghai', 'https://via.placeholder.com/1920x1080?text=Shanghai', 200, 'Yimou Zhang', 'Gong Li, Li Baotian', 108, 7.3, NULL),
(26, 'Dangerous Minds (1995)', 1995, 'A former U.S. Marine becomes a teacher at an inner city school.', 'USA', 'https://via.placeholder.com/300x450?text=Dangerous', 'https://via.placeholder.com/1920x1080?text=Dangerous', 1000, 'John N. Smith', 'Michelle Pfeiffer, George Dzundza', 99, 6.6, NULL),
(27, 'Twelve Monkeys (1995)', 1995, 'In a future world devastated by disease, a convict is sent back in time.', 'USA', 'https://via.placeholder.com/300x450?text=Twelve', 'https://via.placeholder.com/1920x1080?text=Twelve', 2200, 'Terry Gilliam', 'Bruce Willis, Madeleine Stowe', 129, 8.0, NULL),
(28, 'Wings of Courage (1995)', 1995, 'A pilot must land his plane in the Andes mountains after an engine failure.', 'France', 'https://via.placeholder.com/300x450?text=Wings', 'https://via.placeholder.com/1920x1080?text=Wings', 100, 'Jean-Jacques Annaud', 'Craig Sheffer, Elizabeth McGovern', 46, 6.1, NULL),
(29, 'Babe (1995)', 1995, 'Babe, a pig raised by sheepdogs, learns to herd sheep with a little help from Farmer Hoggett.', 'Australia', 'https://via.placeholder.com/300x450?text=Babe', 'https://via.placeholder.com/1920x1080?text=Babe', 1500, 'Chris Noonan', 'James Cromwell, Magda Szubanski', 91, 6.8, NULL),
(30, 'Carrington (1995)', 1995, 'A portrait of a platonic friendship between artist Dora Carrington and writer Lytton Strachey.', 'UK', 'https://via.placeholder.com/300x450?text=Carrington', 'https://via.placeholder.com/1920x1080?text=Carrington', 200, 'Christopher Hampton', 'Emma Thompson, Jonathan Pryce', 122, 6.8, NULL),
(31, 'Dead Man Walking (1995)', 1995, 'A nun, while comforting a convicted killer on death row, empathizes with both the killer and his victim''s families.', 'USA', 'https://via.placeholder.com/300x450?text=Dead', 'https://via.placeholder.com/1920x1080?text=Dead', 1800, 'Tim Robbins', 'Susan Sarandon, Sean Penn', 122, 7.5, NULL),
(32, 'Across the Sea of Time (1995)', 1995, 'A young immigrant boy in 1910 New York City tries to find his family with the help of a magical camera.', 'USA', 'https://via.placeholder.com/300x450?text=Across', 'https://via.placeholder.com/1920x1080?text=Across', 50, 'Stephen Low', 'Philippe Noiret, Lynne Adams', 45, 6.8, NULL),
(33, 'It Takes Two (1995)', 1995, 'A man and a woman, each engaged to someone else, find themselves falling in love during a weekend trip.', 'USA', 'https://via.placeholder.com/300x450?text=Takes', 'https://via.placeholder.com/1920x1080?text=Takes', 600, 'Andy Tennant', 'Kirstie Alley, Steve Guttenberg', 101, 5.8, NULL),
(34, 'Clueless (1995)', 1995, 'A rich high school student tries to boost a new pupil''s popularity, but reckons without affairs of the heart.', 'USA', 'https://via.placeholder.com/300x450?text=Clueless', 'https://via.placeholder.com/1920x1080?text=Clueless', 2000, 'Amy Heckerling', 'Alicia Silverstone, Stacey Dash', 97, 6.8, NULL),
(35, 'Cry, the Beloved Country (1995)', 1995, 'A South African preacher goes to search for his son in Johannesburg.', 'South Africa', 'https://via.placeholder.com/300x450?text=Cry', 'https://via.placeholder.com/1920x1080?text=Cry', 100, 'Darrell Roodt', 'James Earl Jones, Richard Harris', 111, 6.8, NULL),
(36, 'Home for the Holidays (1995)', 1995, 'After losing her job, making her boyfriend break up with her, Claudia Larson has to go home for Thanksgiving dinner.', 'USA', 'https://via.placeholder.com/300x450?text=Home', 'https://via.placeholder.com/1920x1080?text=Home', 800, 'Jodie Foster', 'Holly Hunter, Robert Downey Jr.', 103, 6.6, NULL),
(37, 'Postman, The (1995)', 1995, 'A drifter becomes a postman in a post-apocalyptic world.', 'USA', 'https://via.placeholder.com/300x450?text=Postman', 'https://via.placeholder.com/1920x1080?text=Postman', 300, 'Kevin Costner', 'Kevin Costner, Will Patton', 177, 5.7, NULL),
(38, 'Smoke (1995)', 1995, 'The owner of a Brooklyn smoke shop and his customers try to cope with the changes in their neighborhood.', 'USA', 'https://via.placeholder.com/300x450?text=Smoke', 'https://via.placeholder.com/1920x1080?text=Smoke', 400, 'Wayne Wang', 'Harvey Keitel, William Hurt', 112, 7.0, NULL),
(39, 'Something to Talk About (1995)', 1995, 'A woman''s life is turned upside down when she discovers her husband is having an affair.', 'USA', 'https://via.placeholder.com/300x450?text=Something', 'https://via.placeholder.com/1920x1080?text=Something', 700, 'Lasse Hallström', 'Julia Roberts, Dennis Quaid', 106, 6.0, NULL),
(40, 'Wild Bill (1995)', 1995, 'The story of Wild Bill Hickok and his friendship with Calamity Jane.', 'USA', 'https://via.placeholder.com/300x450?text=Wild', 'https://via.placeholder.com/1920x1080?text=Wild', 200, 'Walter Hill', 'Jeff Bridges, Ellen Barkin', 98, 6.4, NULL),
(41, 'Bridges of Madison County, The (1995)', 1995, 'Photographer Robert Kincaid wanders into the life of housewife Francesca Johnson.', 'USA', 'https://via.placeholder.com/300x450?text=Bridges', 'https://via.placeholder.com/1920x1080?text=Bridges', 2500, 'Clint Eastwood', 'Clint Eastwood, Meryl Streep', 135, 7.6, NULL),
(42, 'Antonia''s Line (1995)', 1995, 'The story of Antonia, who returns to her Dutch hometown after World War II with her daughter Danielle.', 'Netherlands', 'https://via.placeholder.com/300x450?text=Antonia', 'https://via.placeholder.com/1920x1080?text=Antonia', 300, 'Marleen Gorris', 'Willeke van Ammelrooy, Els Dottermans', 102, 7.7, NULL),
(43, 'Angels and Insects (1995)', 1995, 'A Victorian naturalist marries into a wealthy family, but finds himself drawn to his wife''s sister.', 'UK', 'https://via.placeholder.com/300x450?text=Angels', 'https://via.placeholder.com/1920x1080?text=Angels', 150, 'Philip Haas', 'Mark Rylance, Kristin Scott Thomas', 117, 6.8, NULL),
(44, 'Muppet Treasure Island (1995)', 1995, 'The Muppets retell the classic tale of Treasure Island.', 'USA', 'https://via.placeholder.com/300x450?text=Muppet', 'https://via.placeholder.com/1920x1080?text=Muppet', 1000, 'Brian Henson', 'Tim Curry, Kevin Bishop', 99, 6.7, NULL),
(45, 'French Twist (1995)', 1995, 'A French woman''s husband leaves her for another woman, so she seduces the other woman.', 'France', 'https://via.placeholder.com/300x450?text=French', 'https://via.placeholder.com/1920x1080?text=French', 200, 'Josiane Balasko', 'Josiane Balasko, Victoria Abril', 107, 6.4, NULL),
(46, 'From Dusk Till Dawn (1995)', 1995, 'Two criminals and their hostages unknowingly seek temporary refuge in an establishment populated by vampires.', 'USA', 'https://via.placeholder.com/300x450?text=Dusk', 'https://via.placeholder.com/1920x1080?text=Dusk', 1800, 'Robert Rodriguez', 'George Clooney, Quentin Tarantino', 108, 7.2, NULL),
(47, 'White Balloon, The (1995)', 1995, 'A little girl''s quest to buy a goldfish for the Persian New Year.', 'Iran', 'https://via.placeholder.com/300x450?text=White', 'https://via.placeholder.com/1920x1080?text=White', 100, 'Jafar Panahi', 'Aida Mohammadkhani, Mohsen Kafili', 85, 7.5, NULL),
(48, 'Three Wishes (1995)', 1995, 'A mysterious stranger helps a family during the Korean War.', 'USA', 'https://via.placeholder.com/300x450?text=Three', 'https://via.placeholder.com/1920x1080?text=Three', 300, 'Martha Coolidge', 'Patrick Swayze, Mary Elizabeth Mastrantonio', 115, 6.1, NULL),
(49, 'Castle Freak (1995)', 1995, 'A family inherits a castle in Italy, but it comes with a hideous secret.', 'USA', 'https://via.placeholder.com/300x450?text=Castle', 'https://via.placeholder.com/1920x1080?text=Castle', 150, 'Stuart Gordon', 'Jeffrey Combs, Barbara Crampton', 95, 5.4, NULL),
(50, 'Mallrats (1995)', 1995, 'Two best friends are dumped by their girlfriends, so they decide to visit the local mall.', 'USA', 'https://via.placeholder.com/300x450?text=Mallrats', 'https://via.placeholder.com/1920x1080?text=Mallrats', 800, 'Kevin Smith', 'Shannen Doherty, Jeremy London', 94, 6.9, NULL),
(51, 'Net, The (1995)', 1995, 'A computer programmer stumbles upon a conspiracy, putting her life and the lives of those around her in great danger.', 'USA', 'https://via.placeholder.com/300x450?text=Net', 'https://via.placeholder.com/1920x1080?text=Net', 1200, 'Irwin Winkler', 'Sandra Bullock, Jeremy Northam', 114, 5.8, NULL),
(52, 'Pocahontas (1995)', 1995, 'An English soldier and the daughter of an Algonquin chief share a romance when English colonists invade seventeenth century Virginia.', 'USA', 'https://via.placeholder.com/300x450?text=Pocahontas', 'https://via.placeholder.com/1920x1080?text=Pocahontas', 2000, 'Mike Gabriel', 'Irene Bedard, Judy Kuhn', 81, 6.7, NULL),
(53, 'When Night Is Falling (1995)', 1995, 'A professor of mythology falls in love with a circus performer.', 'Canada', 'https://via.placeholder.com/300x450?text=Night', 'https://via.placeholder.com/1920x1080?text=Night', 100, 'Patricia Rozema', 'Pascale Bussières, Rachael Crawford', 94, 6.8, NULL),
(54, 'Usual Suspects, The (1995)', 1995, 'A sole survivor tells of the twisty events leading up to a horrific gun battle on a boat.', 'USA', 'https://via.placeholder.com/300x450?text=Usual', 'https://via.placeholder.com/1920x1080?text=Usual', 3000, 'Bryan Singer', 'Kevin Spacey, Gabriel Byrne', 106, 8.5, NULL),
(55, 'Guardian Angel (1995)', 1995, 'A guardian angel helps a young woman find love.', 'USA', 'https://via.placeholder.com/300x450?text=Guardian', 'https://via.placeholder.com/1920x1080?text=Guardian', 50, 'Richard W. Haines', 'Cynthia Gibb, Dwier Brown', 90, 4.8, NULL),
(56, 'Mighty Aphrodite (1995)', 1995, 'A New York sportswriter is determined to find the biological mother of his adopted son.', 'USA', 'https://via.placeholder.com/300x450?text=Mighty', 'https://via.placeholder.com/1920x1080?text=Mighty', 800, 'Woody Allen', 'Woody Allen, Mira Sorvino', 95, 7.0, NULL),
(57, 'Lamerica (1995)', 1995, 'Two Italian con men travel to Albania to start a fake shoe company.', 'Italy', 'https://via.placeholder.com/300x450?text=Lamerica', 'https://via.placeholder.com/1920x1080?text=Lamerica', 100, 'Gianni Amelio', 'Enrico Lo Verso, Carmelo Di Mazzarelli', 125, 7.3, NULL),
(58, 'Big Green, The (1995)', 1995, 'A British soccer coach comes to a small Texas town and helps the local kids form a soccer team.', 'USA', 'https://via.placeholder.com/300x450?text=Big', 'https://via.placeholder.com/1920x1080?text=Big', 600, 'Holly Goldberg Sloan', 'Steve Guttenberg, Olivia d''Abo', 100, 5.8, NULL),
(59, 'Georgia (1995)', 1995, 'A young woman tries to make it as a singer while dealing with her troubled relationship with her sister.', 'USA', 'https://via.placeholder.com/300x450?text=Georgia', 'https://via.placeholder.com/1920x1080?text=Georgia', 200, 'Ulu Grosbard', 'Jennifer Jason Leigh, Mare Winningham', 115, 6.8, NULL),
(60, 'Kids of the Round Table (1995)', 1995, 'A group of kids form their own version of the Knights of the Round Table.', 'USA', 'https://via.placeholder.com/300x450?text=Kids', 'https://via.placeholder.com/1920x1080?text=Kids', 100, 'Robert Tinnell', 'Malcolm McDowell, Art Hindle', 90, 5.4, NULL),
(61, 'Home Alone 3 (1995)', 1995, 'A young boy must protect his house from a group of international terrorists.', 'USA', 'https://via.placeholder.com/300x450?text=Home3', 'https://via.placeholder.com/1920x1080?text=Home3', 1500, 'Raja Gosnell', 'Alex D. Linz, Olek Krupa', 102, 4.3, NULL),
(62, 'Houseguest (1995)', 1995, 'A con man poses as a long-lost friend to avoid the mob.', 'USA', 'https://via.placeholder.com/300x450?text=Houseguest', 'https://via.placeholder.com/1920x1080?text=Houseguest', 400, 'Randall Miller', 'Sinbad, Phil Hartman', 109, 5.4, NULL),
(63, 'Heavyweights (1995)', 1995, 'A group of overweight kids are sent to a fat camp run by a sadistic counselor.', 'USA', 'https://via.placeholder.com/300x450?text=Heavyweights', 'https://via.placeholder.com/1920x1080?text=Heavyweights', 500, 'Steven Brill', 'Tom McGowan, Aaron Schwartz', 100, 6.4, NULL),
(64, 'Miracle on 34th Street (1995)', 1995, 'A young girl''s belief in Santa Claus is restored when she meets a man who claims to be the real Santa.', 'USA', 'https://via.placeholder.com/300x450?text=Miracle', 'https://via.placeholder.com/1920x1080?text=Miracle', 800, 'Les Mayfield', 'Richard Attenborough, Elizabeth Perkins', 114, 6.6, NULL),
(65, 'Tales from the Hood (1995)', 1995, 'Four horror stories are told by a funeral director to three drug dealers.', 'USA', 'https://via.placeholder.com/300x450?text=Tales', 'https://via.placeholder.com/1920x1080?text=Tales', 300, 'Rusty Cundieff', 'Clarence Williams III, Joe Torry', 98, 6.0, NULL),
(66, 'Vampire in Brooklyn (1995)', 1995, 'A vampire comes to Brooklyn to find his mate.', 'USA', 'https://via.placeholder.com/300x450?text=Vampire', 'https://via.placeholder.com/1920x1080?text=Vampire', 400, 'Wes Craven', 'Eddie Murphy, Angela Bassett', 100, 5.5, NULL),
(67, 'Stuart Saves His Family (1995)', 1995, 'Stuart Smalley tries to help his dysfunctional family.', 'USA', 'https://via.placeholder.com/300x450?text=Stuart', 'https://via.placeholder.com/1920x1080?text=Stuart', 200, 'Harold Ramis', 'Al Franken, Laura San Giacomo', 95, 5.4, NULL),
(68, 'Congo (1995)', 1995, 'An expedition to the Congo to find a lost city and its diamond mines.', 'USA', 'https://via.placeholder.com/300x450?text=Congo', 'https://via.placeholder.com/1920x1080?text=Congo', 1000, 'Frank Marshall', 'Laura Linney, Dylan Walsh', 109, 5.3, NULL),
(69, 'Casper (1995)', 1995, 'A friendly ghost helps a young girl and her father.', 'USA', 'https://via.placeholder.com/300x450?text=Casper', 'https://via.placeholder.com/1920x1080?text=Casper', 1500, 'Brad Silberling', 'Bill Pullman, Christina Ricci', 100, 6.0, NULL),
(70, 'Batman Forever (1995)', 1995, 'Batman must battle Two-Face and The Riddler with help from an amorous psychologist and a young circus acrobat.', 'USA', 'https://via.placeholder.com/300x450?text=Batman', 'https://via.placeholder.com/1920x1080?text=Batman', 2500, 'Joel Schumacher', 'Val Kilmer, Tommy Lee Jones', 121, 5.4, NULL),
(71, 'Apollo 13 (1995)', 1995, 'NASA must devise a strategy to return Apollo 13 to Earth safely after the spacecraft undergoes massive internal damage.', 'USA', 'https://via.placeholder.com/300x450?text=Apollo', 'https://via.placeholder.com/1920x1080?text=Apollo', 3000, 'Ron Howard', 'Tom Hanks, Bill Paxton', 140, 7.7, NULL),
(72, 'Rob Roy (1995)', 1995, 'In 1713 Scotland, Rob Roy MacGregor is wronged by a nobleman and his nephew.', 'UK', 'https://via.placeholder.com/300x450?text=Rob', 'https://via.placeholder.com/1920x1080?text=Rob', 1200, 'Michael Caton-Jones', 'Liam Neeson, Jessica Lange', 139, 7.1, NULL),
(73, 'Addiction, The (1995)', 1995, 'A graduate student becomes a vampire after being bitten by one.', 'USA', 'https://via.placeholder.com/300x450?text=Addiction', 'https://via.placeholder.com/1920x1080?text=Addiction', 100, 'Abel Ferrara', 'Lili Taylor, Christopher Walken', 82, 6.4, NULL),
(74, 'Outbreak (1995)', 1995, 'Army doctors struggle to find a cure for a deadly virus spreading throughout a California town.', 'USA', 'https://via.placeholder.com/300x450?text=Outbreak', 'https://via.placeholder.com/1920x1080?text=Outbreak', 1800, 'Wolfgang Petersen', 'Dustin Hoffman, Rene Russo', 128, 6.6, NULL),
(75, 'Professional, The (1995)', 1995, 'Mathilda, a 12-year-old girl, is reluctantly taken in by Léon, a professional assassin.', 'France', 'https://via.placeholder.com/300x450?text=Professional', 'https://via.placeholder.com/1920x1080?text=Professional', 2800, 'Luc Besson', 'Jean Reno, Gary Oldman', 110, 8.5, NULL),
(76, 'Safe (1995)', 1995, 'A suburban housewife develops a mysterious illness that doctors cannot diagnose.', 'USA', 'https://via.placeholder.com/300x450?text=Safe', 'https://via.placeholder.com/1920x1080?text=Safe', 200, 'Todd Haynes', 'Julianne Moore, Xander Berkeley', 119, 7.0, NULL),
(77, 'Browning Version, The (1995)', 1995, 'A retiring schoolmaster reflects on his life and career.', 'UK', 'https://via.placeholder.com/300x450?text=Browning', 'https://via.placeholder.com/1920x1080?text=Browning', 150, 'Mike Figgis', 'Albert Finney, Greta Scacchi', 97, 7.1, NULL),
(78, 'Shallow Grave (1995)', 1995, 'Three friends discover their new flatmate dead but loaded with cash.', 'UK', 'https://via.placeholder.com/300x450?text=Shallow', 'https://via.placeholder.com/1920x1080?text=Shallow', 400, 'Danny Boyle', 'Kerry Fox, Christopher Eccleston', 92, 7.2, NULL),
(79, 'Reckless (1995)', 1995, 'A woman''s life is turned upside down when she discovers her husband is having an affair.', 'USA', 'https://via.placeholder.com/300x450?text=Reckless', 'https://via.placeholder.com/1920x1080?text=Reckless', 100, 'Norman René', 'Mia Farrow, Scott Glenn', 90, 6.4, NULL),
(80, 'Dolores Claiborne (1995)', 1995, 'A housekeeper suspected of killing her wealthy employer is also suspected of killing her own husband years earlier.', 'USA', 'https://via.placeholder.com/300x450?text=Dolores', 'https://via.placeholder.com/1920x1080?text=Dolores', 800, 'Taylor Hackford', 'Kathy Bates, Jennifer Jason Leigh', 131, 7.1, NULL),
(81, 'Restoration (1995)', 1995, 'A young doctor in 17th century England is exiled to the countryside after falling from grace.', 'UK', 'https://via.placeholder.com/300x450?text=Restoration', 'https://via.placeholder.com/1920x1080?text=Restoration', 300, 'Michael Hoffman', 'Robert Downey Jr., Sam Neill', 118, 6.8, NULL),
(82, 'Mortal Kombat (1995)', 1995, 'Three martial artists are summoned to a mysterious island to compete in a tournament.', 'USA', 'https://via.placeholder.com/300x450?text=Mortal', 'https://via.placeholder.com/1920x1080?text=Mortal', 2000, 'Paul W.S. Anderson', 'Christopher Lambert, Robin Shou', 101, 5.8, NULL),
(83, 'Pocahontas II: Journey to a New World (1995)', 1995, 'Pocahontas travels to England to prevent a war between the English and the Native Americans.', 'USA', 'https://via.placeholder.com/300x450?text=Pocahontas2', 'https://via.placeholder.com/1920x1080?text=Pocahontas2', 500, 'Bradley Raymond', 'Irene Bedard, Billy Zane', 72, 5.4, NULL),
(84, 'To Die For (1995)', 1995, 'A weather reporter kills her husband to advance her career.', 'USA', 'https://via.placeholder.com/300x450?text=ToDie', 'https://via.placeholder.com/1920x1080?text=ToDie', 600, 'Gus Van Sant', 'Nicole Kidman, Matt Dillon', 106, 6.8, NULL),
(85, 'Tommy Boy (1995)', 1995, 'An incompetent, immature, and dimwitted heir to an auto parts factory must save the business.', 'USA', 'https://via.placeholder.com/300x450?text=Tommy', 'https://via.placeholder.com/1920x1080?text=Tommy', 1500, 'Peter Segal', 'Chris Farley, David Spade', 97, 7.1, NULL),
(86, 'Village of the Damned (1995)', 1995, 'A small town''s women give birth to identical children who are not quite human.', 'USA', 'https://via.placeholder.com/300x450?text=Village', 'https://via.placeholder.com/1920x1080?text=Village', 400, 'John Carpenter', 'Christopher Reeve, Kirstie Alley', 99, 5.4, NULL),
(87, 'Under Siege 2: Dark Territory (1995)', 1995, 'Casey Ryback is on a train when terrorists hijack it.', 'USA', 'https://via.placeholder.com/300x450?text=Under', 'https://via.placeholder.com/1920x1080?text=Under', 800, 'Geoff Murphy', 'Steven Seagal, Eric Bogosian', 100, 5.4, NULL),
(88, 'Waterworld (1995)', 1995, 'In a future where the polar ice-caps have melted and Earth is almost entirely submerged.', 'USA', 'https://via.placeholder.com/300x450?text=Waterworld', 'https://via.placeholder.com/1920x1080?text=Waterworld', 1500, 'Kevin Reynolds', 'Kevin Costner, Dennis Hopper', 135, 6.1, NULL),
(89, 'White Man''s Burden (1995)', 1995, 'In an alternate America where African Americans are the majority and whites are the minority.', 'USA', 'https://via.placeholder.com/300x450?text=White', 'https://via.placeholder.com/1920x1080?text=White', 200, 'Desmond Nakano', 'John Travolta, Harry Belafonte', 89, 5.8, NULL),
(90, 'Wild Bunch, The (1995)', 1995, 'An aging group of outlaws look for one last big score as the traditional American West is disappearing.', 'USA', 'https://via.placeholder.com/300x450?text=WildBunch', 'https://via.placeholder.com/1920x1080?text=WildBunch', 300, 'Sam Peckinpah', 'William Holden, Ernest Borgnine', 145, 8.0, NULL),
(91, 'Wings of the Dove, The (1995)', 1995, 'A young woman in 1910 London must choose between love and money.', 'UK', 'https://via.placeholder.com/300x450?text=WingsDove', 'https://via.placeholder.com/1920x1080?text=WingsDove', 400, 'Iain Softley', 'Helena Bonham Carter, Linus Roache', 102, 7.0, NULL),
(92, 'Bad Boys (1995)', 1995, 'Two hip detectives protect a witness to a murder while investigating a case of stolen heroin.', 'USA', 'https://via.placeholder.com/300x450?text=BadBoys', 'https://via.placeholder.com/1920x1080?text=BadBoys', 2000, 'Michael Bay', 'Will Smith, Martin Lawrence', 119, 6.8, NULL),
(93, 'Braveheart (1995)', 1995, 'Scottish warrior William Wallace leads his countrymen in a rebellion to free his homeland.', 'USA', 'https://via.placeholder.com/300x450?text=Braveheart', 'https://via.placeholder.com/1920x1080?text=Braveheart', 3500, 'Mel Gibson', 'Mel Gibson, Sophie Marceau', 178, 8.3, NULL),
(94, 'Canadian Bacon (1995)', 1995, 'The U.S. president declares war on Canada to boost his approval ratings.', 'USA', 'https://via.placeholder.com/300x450?text=Canadian', 'https://via.placeholder.com/1920x1080?text=Canadian', 300, 'Michael Moore', 'John Candy, Rhea Perlman', 91, 5.8, NULL),
(95, 'Crimson Tide (1995)', 1995, 'On a U.S. nuclear missile sub, a young first officer stages a mutiny to prevent his trigger happy captain.', 'USA', 'https://via.placeholder.com/300x450?text=Crimson', 'https://via.placeholder.com/1920x1080?text=Crimson', 1800, 'Tony Scott', 'Denzel Washington, Gene Hackman', 116, 7.3, NULL),
(96, 'Desperado (1995)', 1995, 'A musician and a beautiful bookstore owner are caught up in a turf war between Mexican drug lords.', 'USA', 'https://via.placeholder.com/300x450?text=Desperado', 'https://via.placeholder.com/1920x1080?text=Desperado', 1500, 'Robert Rodriguez', 'Antonio Banderas, Salma Hayek', 104, 7.2, NULL),
(97, 'Die Hard: With a Vengeance (1995)', 1995, 'John McClane and a Harlem store owner are targeted by German terrorist Simon in New York City.', 'USA', 'https://via.placeholder.com/300x450?text=DieHard', 'https://via.placeholder.com/1920x1080?text=DieHard', 2500, 'John McTiernan', 'Bruce Willis, Jeremy Irons', 128, 7.6, NULL),
(98, 'Empire Records (1995)', 1995, 'The employees of an independent music store learn about each other as they try to keep the store.', 'USA', 'https://via.placeholder.com/300x450?text=Empire', 'https://via.placeholder.com/1920x1080?text=Empire', 600, 'Allan Moyle', 'Anthony LaPaglia, Debi Mazar', 90, 6.7, NULL),
(99, 'First Knight (1995)', 1995, 'A young man becomes a knight and falls in love with a princess.', 'USA', 'https://via.placeholder.com/300x450?text=First', 'https://via.placeholder.com/1920x1080?text=First', 800, 'Jerry Zucker', 'Sean Connery, Richard Gere', 134, 6.0, NULL),
(100, 'Free Willy 2: The Adventure Home (1995)', 1995, 'Jesse and Willy are reunited when the whale is threatened by an oil spill.', 'USA', 'https://via.placeholder.com/300x450?text=FreeWilly2', 'https://via.placeholder.com/1920x1080?text=FreeWilly2', 700, 'Dwight H. Little', 'Jason James Richter, August Schellenberg', 98, 4.8, NULL),
(101, 'Hackers (1995)', 1995, 'A young hacker is accused of unleashing a computer virus.', 'USA', 'https://via.placeholder.com/300x450?text=Hackers', 'https://via.placeholder.com/1920x1080?text=Hackers', 1000, 'Iain Softley', 'Jonny Lee Miller, Angelina Jolie', 107, 6.2, NULL);
GO

-- Disable IDENTITY_INSERT
SET IDENTITY_INSERT cine.Movie OFF;
GO

-- Insert genres only if they don't exist
INSERT INTO cine.Genre (name) 
SELECT 'Action' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Action')
UNION ALL SELECT 'Adventure' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Adventure')
UNION ALL SELECT 'Animation' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Animation')
UNION ALL SELECT 'Children' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Children')
UNION ALL SELECT 'Comedy' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Comedy')
UNION ALL SELECT 'Crime' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Crime')
UNION ALL SELECT 'Documentary' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Documentary')
UNION ALL SELECT 'Drama' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Drama')
UNION ALL SELECT 'Fantasy' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Fantasy')
UNION ALL SELECT 'Film-Noir' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Film-Noir')
UNION ALL SELECT 'Horror' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Horror')
UNION ALL SELECT 'Musical' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Musical')
UNION ALL SELECT 'Mystery' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Mystery')
UNION ALL SELECT 'Romance' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Romance')
UNION ALL SELECT 'Sci-Fi' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Sci-Fi')
UNION ALL SELECT 'Thriller' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Thriller')
UNION ALL SELECT 'War' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'War')
UNION ALL SELECT 'Western' WHERE NOT EXISTS (SELECT 1 FROM cine.Genre WHERE name = 'Western');
GO

-- Insert movie-genre relationships (sample for first 20 movies)
INSERT INTO cine.MovieGenre (movieId, genreId) 
SELECT 2, g.genreId FROM cine.Genre g WHERE g.name = 'Action'
UNION ALL SELECT 2, g.genreId FROM cine.Genre g WHERE g.name = 'Adventure'
UNION ALL SELECT 2, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 2, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 2, g.genreId FROM cine.Genre g WHERE g.name = 'Fantasy'
UNION ALL SELECT 3, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 3, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 3, g.genreId FROM cine.Genre g WHERE g.name = 'Romance'
UNION ALL SELECT 4, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 4, g.genreId FROM cine.Genre g WHERE g.name = 'Romance'
UNION ALL SELECT 5, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 5, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 5, g.genreId FROM cine.Genre g WHERE g.name = 'Romance'
UNION ALL SELECT 6, g.genreId FROM cine.Genre g WHERE g.name = 'Action'
UNION ALL SELECT 6, g.genreId FROM cine.Genre g WHERE g.name = 'Crime'
UNION ALL SELECT 6, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 7, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 7, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 7, g.genreId FROM cine.Genre g WHERE g.name = 'Romance'
UNION ALL SELECT 8, g.genreId FROM cine.Genre g WHERE g.name = 'Action'
UNION ALL SELECT 8, g.genreId FROM cine.Genre g WHERE g.name = 'Adventure'
UNION ALL SELECT 8, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 8, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 9, g.genreId FROM cine.Genre g WHERE g.name = 'Action'
UNION ALL SELECT 9, g.genreId FROM cine.Genre g WHERE g.name = 'Crime'
UNION ALL SELECT 9, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 10, g.genreId FROM cine.Genre g WHERE g.name = 'Action'
UNION ALL SELECT 10, g.genreId FROM cine.Genre g WHERE g.name = 'Adventure'
UNION ALL SELECT 10, g.genreId FROM cine.Genre g WHERE g.name = 'Crime'
UNION ALL SELECT 10, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 11, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 11, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 11, g.genreId FROM cine.Genre g WHERE g.name = 'Romance'
UNION ALL SELECT 12, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 12, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 12, g.genreId FROM cine.Genre g WHERE g.name = 'Fantasy'
UNION ALL SELECT 13, g.genreId FROM cine.Genre g WHERE g.name = 'Adventure'
UNION ALL SELECT 13, g.genreId FROM cine.Genre g WHERE g.name = 'Animation'
UNION ALL SELECT 13, g.genreId FROM cine.Genre g WHERE g.name = 'Children'
UNION ALL SELECT 13, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 14, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 14, g.genreId FROM cine.Genre g WHERE g.name = 'War'
UNION ALL SELECT 15, g.genreId FROM cine.Genre g WHERE g.name = 'Action'
UNION ALL SELECT 15, g.genreId FROM cine.Genre g WHERE g.name = 'Adventure'
UNION ALL SELECT 15, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 15, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 16, g.genreId FROM cine.Genre g WHERE g.name = 'Crime'
UNION ALL SELECT 16, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 16, g.genreId FROM cine.Genre g WHERE g.name = 'Thriller'
UNION ALL SELECT 17, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 17, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 17, g.genreId FROM cine.Genre g WHERE g.name = 'Romance'
UNION ALL SELECT 18, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 18, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 18, g.genreId FROM cine.Genre g WHERE g.name = 'Fantasy'
UNION ALL SELECT 19, g.genreId FROM cine.Genre g WHERE g.name = 'Action'
UNION ALL SELECT 19, g.genreId FROM cine.Genre g WHERE g.name = 'Adventure'
UNION ALL SELECT 19, g.genreId FROM cine.Genre g WHERE g.name = 'Comedy'
UNION ALL SELECT 19, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 20, g.genreId FROM cine.Genre g WHERE g.name = 'Drama'
UNION ALL SELECT 20, g.genreId FROM cine.Genre g WHERE g.name = 'Fantasy'
UNION ALL SELECT 20, g.genreId FROM cine.Genre g WHERE g.name = 'Horror';
GO

-- Verify import
SELECT COUNT(*) as TotalMovies FROM cine.Movie;
SELECT COUNT(*) as TotalGenres FROM cine.Genre;
SELECT COUNT(*) as TotalMovieGenres FROM cine.MovieGenre;
GO

PRINT 'Import completed successfully!';
PRINT 'You can now run: python train_content_based.py';
