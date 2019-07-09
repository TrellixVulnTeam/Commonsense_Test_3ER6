from __future__ import print_function
import numpy, os, spacy, pickle, sys, re, random
from itertools import *
import multiprocessing

# load spacy model for nlp tools
encoder = spacy.load('en_core_web_md')

pos_tag_idxs = {'#': 1, '$': 2, "''": 3, '(': 4, ')': 5, ',': 6, '-LRB-': 7, '-PRB-': 8, '.': 9, ':': 10, 'ADD': 11,
                'AFX': 12, 'BES': 13, 'CC': 14, 'CD': 15, 'DT': 16, 'EX': 17,
                'FW': 18, 'GW': 19, 'HVS': 20, 'HYPH': 21, 'IN': 22, 'JJ': 23, 'JJR': 24, 'JJS': 25, 'LS': 26, 'MD': 27,
                'NFP': 28, 'NIL': 29, 'NN': 30, 'NNP': 31, 'NNPS': 32, 'NNS': 33,
                'PDT': 34, 'POS': 35, 'PRP': 36, 'PRP$': 37, 'RB': 38, 'RBR': 39, 'RBS': 40, 'RP': 41, 'SP': 42,
                'SYM': 43, 'TO': 44, 'UH': 45, 'VB': 46, 'VBD': 47, 'VBG': 48, 'VBN': 49,
                'VBP': 50, 'VBZ': 51, 'WDT': 52, 'WP': 53, 'WP$': 54, 'WRB': 55, 'XX': 56, '``': 57, '""': 58,
                '-RRB-': 59}

rng = numpy.random.RandomState(0)


def segment(seq, clauses=False):
    if clauses:
        seq = segment_into_clauses(seq)  # segment into clauses rather than just sentences
    else:
        seq = [sent.string.strip() for sent in encoder(seq).sents]
    return seq


def tokenize(seq, lowercase=True, recognize_ents=False, lemmatize=False, include_tags=[], include_pos=[],
             prepend_start=False):
    seq = encoder(seq)  # 用spacy
    if recognize_ents:  # merge named entities into single tokens
        ent_start_idxs = {ent.start: ent for ent in seq.ents if ent.string.strip()}
        # combine each ent into a single token; this is pretty hard to read, but it works
        seq = [ent_start_idxs[word_idx] if word_idx in ent_start_idxs else word
               for word_idx, word in enumerate(seq)
               if (not word.ent_type_ or word_idx in ent_start_idxs)]
    # Don't apply POS filtering to phrases (words with underscores)
    if include_tags:
        # fine-grained POS tags
        seq = [word for word in seq if ("_" in word.string or word.tag_ in include_tags)]
    if include_pos:
        # coarse-grained POS tags
        seq = [word for word in seq if ("_" in word.string or word.pos_ in include_pos)]
    if lemmatize:
        seq = [word.lemma_ if not word.string.startswith('ENT_') else word.string.strip() for word in seq]
    elif lowercase:
        # don't lowercase if token is an entity (entities will be of type span instead of token;
        # or will be prefixed with 'ENT_' if already transformed to types)
        seq = [word.string.strip().lower() if (type(word) == spacy.tokens.token.Token and not word.string.startswith('ENT_'))
               else word.string.strip() for word in seq]
    else:
        seq = [word.string.strip() for word in seq]
    seq = [word for word in seq if word]  # some words may be empty strings, so filter
    if prepend_start:
        seq.insert(0, u"<START>")
    return seq


def get_pos_num_seq(seq):  # get part-of-speech (PTB fine-grained) tags for this sequence, converted to indices
    seq = encoder(seq)
    pos_num_seq = [pos_tag_idxs[word.tag_] if not word.string.startswith('ENT_') else 'NNP' for word in
                   seq]  # if token is an entity, assume POS is proper noun
    assert (numpy.all(numpy.array(pos_num_seq) > 0))
    assert (len(seq) == len(pos_num_seq))
    return pos_num_seq


def ent_counts_to_probs(ent_counts):
    sum_ent_counts = {ent_type: sum(counts.values()) for ent_type, counts in ent_counts.items()}
    ent_probs = {ent_type: {ent: count * 1. / sum_ent_counts[ent_type] for ent, count in ent_counts[ent_type].items()}
                 for ent_type in ent_counts}
    return ent_probs


def get_ents(seq, include_ent_types=('PERSON', 'NORP', 'ORG', 'GPE'), recognize_gender=False,
             gender_filenames={'FEMALE': 'female_names.pkl', 'MALE': 'male_names.pkl'}):
    '''return dict of all entities in seq mapped to their entity types, optionally labeled with gender for PERSON entities'''

    if recognize_gender:
        names_gender = {}
        for gender, filename in gender_filenames.items():
            with open(filename) as f:
                names_gender[gender] = pickle.load(f)
    ents = {}
    ent_counts = {}
    for ent in encoder(seq).ents:
        ent_type = ent.label_
        if ent_type in include_ent_types:
            ent = ent.string.strip()
            if ent:  # not sure why, but whitespace can be detected as an ent, so need to check for this
                ents[ent] = [ent_type]
                if ent in ent_counts:
                    ent_counts[ent] += 1
                else:
                    ent_counts[ent] = 1
                if recognize_gender and ent_type == 'PERSON':  # append gender to entity type
                    detected_gender = None
                    for gender, names in names_gender.items():
                        if ent.split()[0] in names:  # person may have multiple tokens in name, just check first name
                            if detected_gender:  # name is associated with both genders, so omit it
                                detected_gender = None
                            else:
                                detected_gender = gender
                    if detected_gender:
                        ents[ent].append(detected_gender)
                ents[ent] = "_".join(ents[ent])
    return ents, ent_counts


def number_ents(ents, ent_counts):
    '''return dict of all entities in seq mapped to their entity types, 
    with numerical suffixes to distinguish entities of the same type'''
    ent_counts = sorted([(count, ent, ents[ent]) for ent, count in ent_counts.items()])[::-1]
    ent_type_counts = {}
    num_ents = {}
    for count, ent, ent_type in ent_counts:
        coref_ent = [num_ent for num_ent in num_ents if
                     (tokenize(num_ent, lowercase=False)[0] == tokenize(ent, lowercase=False)[0]
                      or tokenize(num_ent, lowercase=False)[-1] == tokenize(ent, lowercase=False)[-1])
                     and ents[num_ent] == ent_type]  # treat ents with same first or last word as co-referring
        if coref_ent:
            num_ents[ent] = num_ents[coref_ent[0]]
        else:
            ent_type = ent_type.split("_")
            if ent_type[0] in ent_type_counts:
                ent_type_counts[ent_type[0]] += 1
            else:
                ent_type_counts[ent_type[0]] = 1
            num_ents[ent] = ent_type
            num_ents[ent].insert(1, str(
                ent_type_counts[ent_type[0]] - 1))  # insert number id after entity type (and before tag, if it exists)
            num_ents[ent] = "_".join(num_ents[ent])
    return num_ents


def adapt_tok_seq_ents(seq, ents={}, sub_ent_probs={}):
    ents = {ent_type: ent for ent, ent_type in ents.items()}  # reverse ents so that types map to names
    adapted_seq_ents = {"_".join(token.split("_")[1:]): None for token in seq if token.startswith('ENT_')}

    if not adapted_seq_ents:
        return seq

    for seq_ent_type in {ent_type: adapted_ent for ent_type, adapted_ent in adapted_seq_ents.items() if
                         not adapted_ent}:
        if seq_ent_type in ents:
            adapted_seq_ents[seq_ent_type] = ents[seq_ent_type]
            del ents[seq_ent_type]

    if ents:
        for seq_ent_type in {ent_type: adapted_ent for ent_type, adapted_ent in adapted_seq_ents.items() if
                             not adapted_ent}:
            for ent_type, ent in ents.items():
                if seq_ent_type.split("_")[0] in ent_type.split("_")[0]:
                    # import pdb;pdb.set_trace()
                    adapted_seq_ents[seq_ent_type] = ents[ent_type]
                    del ents[ent_type]
                    break

    for seq_ent_type in {ent_type: adapted_ent for ent_type, adapted_ent in adapted_seq_ents.items() if
                         not adapted_ent}:
        if seq_ent_type.split("_")[0] in sub_ent_probs:
            sub_ents, sub_probs = zip(*sub_ent_probs[seq_ent_type.split("_")[0]].items())
            rand_ent_idx = rng.choice(len(sub_ents), p=numpy.array(sub_probs))
            adapted_seq_ents[seq_ent_type] = sub_ents[rand_ent_idx]

    for seq_ent_type in {ent_type: adapted_ent for ent_type, adapted_ent in adapted_seq_ents.items() if
                         not adapted_ent}:
        adapted_seq_ents[seq_ent_type] = u'<UNK>'  # as last resort, replace entity with UNK token

    seq = [adapted_seq_ents["_".join(token.split("_")[1:])] if "_".join(
        token.split("_")[1:]) in adapted_seq_ents else token for token in seq]
    return seq


def detokenize_tok_seq(seq, ents=[]):
    '''use simple rules for transforming list of tokens back into string
    ents is optional list of words (named entities) that should be capitalized'''
    # if type(seq) in (str, unicode):
    #     seq = tokenize(seq, lowercase=False)
    seq = [sent.split() for sent in segment(" ".join(seq))]  # split sequence into sentences
    detok_seq = []
    for sent_idx, sent in enumerate(seq):

        assert (type(sent) in (list, tuple))

        if ents:
            token_idx = 0
            while token_idx < len(sent):  # capitalize all tokens that appear in cap_ents
                for ent in ents:
                    ent = ent.split()
                    if sent[token_idx:token_idx + len(ent)] == [token.lower() for token in ent]:
                        # import pdb;pdb.set_trace()
                        sent[token_idx:token_idx + len(ent)] = list(ent)
                        token_idx += len(ent) - 1
                        break
                token_idx += 1

        detok_sent = " ".join(sent)

        detok_sent = re.sub("\'", "'", detok_sent)

        # capitalize first-person "I" pronoun
        detok_sent = re.sub(" i ", " I ", detok_sent)

        # rules for contractions
        detok_sent = re.sub(" n\'\s*t ", "n\'t ", detok_sent)
        detok_sent = re.sub(" \'\s*d ", "\'d ", detok_sent)
        detok_sent = re.sub(" \'\s*s ", "\'s ", detok_sent)
        detok_sent = re.sub(" \'\s*ve ", "\'ve ", detok_sent)
        detok_sent = re.sub(" \'\s*ll ", "\'ll ", detok_sent)
        detok_sent = re.sub(" \'\s*m ", "\'m ", detok_sent)
        detok_sent = re.sub(" \'\s*re ", "\'re ", detok_sent)

        # rules for formatting punctuation
        detok_sent = re.sub(" \.", ".", detok_sent)
        detok_sent = re.sub(" \!", "!", detok_sent)
        detok_sent = re.sub(" \?", "?", detok_sent)
        detok_sent = re.sub(" ,", ",", detok_sent)
        detok_sent = re.sub(" \- ", "-", detok_sent)
        detok_sent = re.sub(" :", ":", detok_sent)
        detok_sent = re.sub(" ;", ";", detok_sent)
        detok_sent = re.sub("\$ ", "$", detok_sent)
        detok_sent = re.sub("\' \'", "\'\'", detok_sent)
        detok_sent = re.sub("\` \`", "\`\`", detok_sent)

        # replace repeated single quotes with double quotation mark.
        detok_sent = re.sub("\'\'", "\"", detok_sent)
        detok_sent = re.sub("\`\`", "\"", detok_sent)

        # filter repetitive characters
        detok_sent = re.sub("([\"\']\s*){2,}", "\" ", detok_sent)

        punc_pairs = {"\'": "\'", "\'": "\'", "`": "\'", "\"": "\"", "(": ")",
                      "[": "]"}  # map each opening puncutation mark to closing mark
        open_punc = []
        char_idx = 0
        while char_idx < len(detok_sent):  # check for quotes and parenthesis
            char = detok_sent[char_idx]
            if open_punc and char == punc_pairs[open_punc[-1]]:  # end quote/parenthesis
                if char_idx > 0 and detok_sent[char_idx - 1] == " ":
                    detok_sent = detok_sent[:char_idx - 1] + detok_sent[char_idx:]
                    open_punc.pop()
            elif char in punc_pairs:
                if char_idx < len(detok_sent) - 1 and detok_sent[char_idx + 1] == " ":
                    open_punc.append(char)
                    detok_sent = detok_sent[:char_idx + 1] + detok_sent[char_idx + 2:]
            if char_idx < len(detok_sent) and detok_sent[char_idx] == char:
                char_idx += 1

        detok_sent = detok_sent.strip()
        for char_idx, char in enumerate(detok_sent):  # capitalize first alphabetic character
            if char.isalpha():
                detok_sent = detok_sent[:char_idx + 1].upper() + detok_sent[char_idx + 1:]
                break
        detok_seq.append(detok_sent)

    detok_seq = " ".join(detok_seq)
    return detok_seq


def filter_gen_seq(seq, n_sents=1, eos_tokens=[]):
    '''given a generated sequence, filter so that only the first n_sents are included in final generated sequence'''
    # import pdb;pdb.set_trace()
    if eos_tokens:
        # if end-of-sentence tokens given, cut off sequence at first occurrence of one of these tokens;
        # otherwise use segmenter to infer sentence boundaries
        seq = encoder(seq)
        for idx, word in enumerate(seq):
            if word.string.strip() in eos_tokens:
                seq = seq[:idx + 1].string.strip()
                break
    else:
        seq = " ".join(segment(seq)[:n_sents])
    return seq


def get_word_pairs(tok_seq1, tok_seq2):
    pairs = [(word1, word2) for word1 in tok_seq1 for word2 in tok_seq2]
    return pairs


def get_adj_pair(seq, segment_clauses=False, max_distance=1, reverse=False, max_sent_length=25):
    if type(seq) in (str, bytes):
        seq = segment(seq, clauses=segment_clauses)  # segment the seq into sentences or clauses

    adj_pairs = []
    for sent_idx in range(len(seq) - 1):
        sent1 = seq[sent_idx]
        if type(sent1) in (str, bytes):
            len_sent1 = len(tokenize(sent1))
        else:
            len_sent1 = len(sent1)
        if len_sent1 and len_sent1 <= max_sent_length:
            for window_idx in range(max_distance):
                if sent_idx + window_idx == len(seq) - 1:  # sent_idx 句子在seq的位置，window_idx 邻居句子的偏移
                    break
                sent2 = seq[sent_idx + window_idx + 1]
                if type(sent2) in (str, bytes):
                    len_sent2 = len(tokenize(sent2))
                else:
                    len_sent2 = len(sent2)
                if len_sent2 and len_sent2 <= max_sent_length:  # filter sentences that are too long
                    if reverse:
                        adj_pairs.append((sent2, sent1))  # if reverse=True, reverse order of sentence pair
                    else:
                        adj_pairs.append((sent1, sent2))

    return adj_pairs


def get_adj_sent_pairs(seqs, segment_clauses=False, max_distance=1, reverse=False, max_sent_length=25):
    '''sequences can be string or transformer into numbers;
    if segment clauses=True, split sequences by clause boundaries rather than sentence boundaries,
    max distance indicates clause window within which pairs will be found
    (e.g. when max_distance = 2, both neighboring clauses and those separated by one other clause will be paired'''
    results = []
    pool = multiprocessing.Pool(processes=int(multiprocessing.cpu_count()))
    for seq in seqs:
        results.append(pool.apply_async(get_adj_pair, (seq, segment_clauses, max_distance, reverse, max_sent_length,)))
    pool.close()
    pool.join()
    pairs = []
    for res in results:
        pairs.extend(res.get())

    return pairs


def reverse_pairs(pairs):
    reversed_pairs = [(seq2, seq1) for seq1, seq2 in pairs]
    return reversed_pairs


def randomize_pairs(pairs):
    seqs = [seq for pair in pairs for seq in pair]
    random_idx_pairs = rng.permutation(len(seqs)).reshape(-1, 2)
    random_pairs = [(seqs[idx1], seqs[idx2]) for idx1, idx2 in random_idx_pairs]
    return random_pairs


def segment_into_clauses(seq):
    '''applies a set of heuristics to segment a sequence (one or more sentences) into clauses
    the clauses are those that would useful for splitting causal events, so not all types clauses will be recognized'''
    clauses = []
    sents = segment(seq)  # 分子句前先分句
    for sent in sents:
        sent = encoder(sent)
        clause_bound_idxs = []
        for word in sent:
            if word.dep_ in ('advcl', 'conj', 'pcomp') and word.head.dep_ in ('ccomp', 'conj', 'ROOT', 'xcomp'):
                # , 'prep', 'relcl','acomp'): #'prep', 'relcl','acomp' newly added
                if clause_bound_idxs and clause_bound_idxs[-1] >= word.left_edge.i:
                    clause_bound_idxs[-1] = word.left_edge.i  # ensure no overlap in clauses
                if not clause_bound_idxs or clause_bound_idxs[-1] + 1 < word.left_edge.i:
                    clause_bound_idxs.append(word.left_edge.i)  # attach single words to previous clause
                clause_bound_idxs.append(word.right_edge.i + 1)
        if clause_bound_idxs and clause_bound_idxs[0] == 1:
            clause_bound_idxs[0] = 0  # merge first word in first clause if split out
        if not clause_bound_idxs or clause_bound_idxs[0]:
            clause_bound_idxs.insert(0, 0)
        if clause_bound_idxs[-1] < len(sent):
            clause_bound_idxs.append(len(sent))  # set clause boundary at end of sentence
        sent_clauses = []
        for idx, next_idx in zip(clause_bound_idxs, clause_bound_idxs[1:]):
            clause = sent[idx:next_idx]  # .string
            if sent_clauses and len(clause) == 1 and clause[-1].pos_ == 'PUNCT':
                # if clause is punctuation, append it to previous clause
                sent_clauses[-1] = sent_clauses[-1] + clause.string
            else:
                sent_clauses.append(clause.string)
        clauses.extend(sent_clauses)
    return clauses


def combine_phrases_in_seq(seq, phrases, lemmatized=False):
    phrased_seq = []
    seq = tokenize(seq, lowercase=False)
    idx = 0
    while idx < len(seq):
        if lemmatized:  # if phrases are lemmatized; if so, lemmatize sequence when tokenizing
            possible_phrase = [word.lemma_ for word in encoder(" ".join(seq[idx:idx + 3]))]
        else:
            possible_phrase = seq[idx:idx + 3]
        if "_".join(possible_phrase) in phrases:  # first try matching 3 word phrase
            phrased_seq.append("_".join(possible_phrase))
            idx += 3
        elif "_".join(possible_phrase[:-1]) in phrases:  # then back off to 2 words
            phrased_seq.append("_".join(possible_phrase[:-1]))
            idx += 2
        else:
            phrased_seq.append(seq[idx])  # just add word if word not part of phrase
            idx += 1
    phrased_seq = " ".join(phrased_seq)  # return as string
    return phrased_seq


def load_seqs(filepath, memmap=False, shape=None):
    if memmap:
        # file was saved as memmap
        seqs = numpy.memmap(filepath, dtype='float64', mode='r', shape=shape)
    else:
        seqs = numpy.load(filepath, mmap_mode='r')
    print("loaded sequences from filepath", filepath)
    return seqs


class SequenceTransformer():
    def __init__(self, min_freq=1, lexicon=[], lemmatize=False, prepend_start=False, include_tags=[], verbose=1,
                 unk_word=u"<UNK>", word_embs=None, use_spacy_embs=False, generalize_ents=False, phrases=None, filepath=None):
        self.unk_word = unk_word  # string representation for unknown words in lexicon
        self.word_embs = word_embs  # use existing word embeddings if given
        self.use_spacy_embs = use_spacy_embs
        if self.word_embs and self.use_spacy_embs:
            self.n_embedding_nodes = self.word_embs.vector_size + encoder.vocab.vectors_length
        elif self.word_embs:
            self.n_embedding_nodes = self.word_embs.vector_size
        elif self.use_spacy_embs:
            self.n_embedding_nodes = encoder.vocab.vectors_length
        self.phrases = phrases
        self.lexicon = lexicon
        self.lexicon_size = None
        self.lemmatize = lemmatize
        self.include_tags = include_tags
        self.word_counts = {}
        self.min_freq = min_freq
        self.verbose = verbose
        self.generalize_ents = generalize_ents  # specify if named entities should be replaced with generic labels
        self.ent_counts = {}
        self.filtered_ent_counts = {}
        self.filepath = filepath
        self.prepend_start = prepend_start
        self.ent_count_sample_threshold = None
        if self.verbose:
            print("Created transformer:", {param: value for param, value in self.__dict__.items() if
                                           param not in ('lexicon', 'word_embs', 'phrases')})
        if self.filepath:  # if filepath given, save transformer
            # save transformer.pkl
            self.save()

    def make_lexicon(self, seqs):
        # regenerate lexicon everytime this function is called; word_counts will persist between calls
        self.lexicon = {}
        self.lexicon[self.unk_word] = 1  # 加入<UNK>，id=1
        for idx, seq in enumerate(seqs):
            print('seq {}...'.format(idx))
            if self.generalize_ents:  # reduce vocab by mapping all named entities to entity labels (e.g. "PERSON_0")
                ents, ent_counts = get_ents(seq)  # first get named entities
                # build a dictionary of entities that can be substituted when a generated entity isn't resolved
                for ent, ent_type in ents.items():
                    if ent_type not in self.ent_counts:
                        self.ent_counts[ent_type] = {}
                    if ent not in self.ent_counts[ent_type]:
                        self.ent_counts[ent_type][ent] = 1
                    else:
                        self.ent_counts[ent_type][ent] += 1
                seq = self.replace_ents_in_seq(seq)
            if hasattr(self, 'phrases') and self.phrases is not None:  # add given phrases to word counts
                # if sequences will be lemmatized, assume that given phrases are lemmatized
                seq = combine_phrases_in_seq(seq, self.phrases, lemmatized=self.lemmatize)
            seq = tokenize(seq, lemmatize=self.lemmatize, include_tags=self.include_tags,
                           prepend_start=self.prepend_start)  # 给每个故事分词 词形还原 词性标注
            for word in seq:
                if word not in self.word_counts:  # 词频词典
                    self.word_counts[word] = 1
                else:
                    self.word_counts[word] += 1

        for word, count in self.word_counts.items():
            # if word is an entity, automatically include it in vocab;
            # otherwise include word if it occurs at least min_freq times
            if count >= self.min_freq or (self.generalize_ents and word.startswith("ENT_")):
                self.lexicon[word] = max(self.lexicon.values()) + 1

        self.lexicon_size = len(self.lexicon.keys())
        self.lexicon_lookup = [None] + [word for index, word in sorted([(index, word) for word, index in
                                        self.lexicon.items()])]  # insert entry for empty timeslot in lexicon lookup
        assert (len(self.lexicon_lookup) == self.lexicon_size + 1)

        if self.generalize_ents:
            # only consider most frequent 5000 entitites of a type when sampling
            ent_min_freqs = {ent_type: sorted(self.ent_counts[ent_type].values())[-5000:][0] for ent_type in self.ent_counts}
            self.filtered_ent_counts = {ent_type: {ent: count for ent, count in self.ent_counts[ent_type].items()
                                                   # filter by frequency
                                                   if count >= ent_min_freqs[ent_type]} for ent_type in self.ent_counts}

        if hasattr(self, 'phrases') and self.phrases is not None:
            # only keep phrases that are in the lexicon
            self.phrases = set([phrase for phrase in list(self.phrases) if phrase in self.lexicon])
        if self.verbose:
            print("added lexicon of", self.lexicon_size, "words with frequency >=", self.min_freq)
        if self.filepath:  # if filepath given, save transformer
            self.save()

    def replace_ents_in_seq(self, seq):
        '''extract entities from seq and replace them with their entity types'''
        ents, ent_counts = get_ents(seq)
        ents = number_ents(ents, ent_counts)
        seq = tokenize(seq, lowercase=False, recognize_ents=True)
        seq = ['ENT_' + ents[word] if word in ents else word for word in seq]
        seq = " ".join(seq)
        return seq

    def tok_seq_to_nums(self, seq):
        assert (type(seq) == list)
        # map each token in list of tokens to an index; if word is None, replace with 0;
        # if word is not None but not in lexicon, replace with 1
        seq = [self.lexicon[word] if word in self.lexicon else 1 if word else 0 for word in seq]
        return seq

    def tok_seqs_to_nums(self, seqs):
        assert (type(seqs[0]) == list)
        num_seqs = []
        for seq in seqs:
            seq = self.tok_seq_to_nums(seq)
            if not seq:
                seq.append(1)  # if seq is blank, represent with single unknown word
            num_seqs.append(seq)
        assert (len(seqs) == len(num_seqs))
        return num_seqs

    def text_to_tok_seqs(self, seqs):
        seqs = [
            tokenize(seq, lemmatize=self.lemmatize, include_tags=self.include_tags, prepend_start=self.prepend_start)
            for seq in seqs]
        return seqs

    def text_to_nums(self, seqs):
        '''tokenize string sequences and convert to list of word indices'''
        # import pdb;pdb.set_trace()
        num_seqs = []
        for seq in seqs:
            if hasattr(self, 'phrases') and self.phrases is not None:
                seq = combine_phrases_in_seq(seq, self.phrases, lemmatized=self.lemmatize)
            seq = tokenize(seq, lemmatize=self.lemmatize, include_tags=self.include_tags,
                           prepend_start=self.prepend_start)
            seq = self.tok_seq_to_nums(seq)
            if not seq:
                seq.append(1)  # if seq is blank, represent with single unknown word
            num_seqs.append(seq)
        assert (len(seqs) == len(num_seqs))
        return num_seqs

    def tok_seq_to_embs(self, seq, reduce_emb_mode=None):
        assert (type(seq) == list)
        if not seq:
            seq = numpy.zeros((1, self.n_embedding_nodes))  # seq has no words
        else:
            if hasattr(self, 'phrases') and self.phrases is not None:
                seq = [word for token in seq for word in
                       token.split("_")]  # join phrases with white space so each word in phrase is given an embedding
            if self.use_spacy_embs and self.word_embs is not None:  # concatenate spacy vectors and vectors for given word embeddings
                seq = numpy.array([numpy.concatenate(
                    [self.word_embs[word] if word in self.word_embs else numpy.zeros((self.word_embs.vector_size)),
                     encoder(word).vector], axis=-1) for word in seq])
            elif self.use_spacy_embs:
                seq = numpy.array([encoder(word).vector for word in seq])
            elif self.word_embs is not None:
                seq = numpy.array(
                    [self.word_embs[word] if word in self.word_embs else numpy.zeros((self.n_embedding_nodes))
                     for word in seq])
        if reduce_emb_mode:  # combine embeddings of each sequence by averaging or summing them
            if reduce_emb_mode == 'mean':
                seq = numpy.mean(seq, axis=0)
            elif reduce_emb_mode == 'sum':
                seq = numpy.sum(seq, axis=0)
        return seq

    def tok_seqs_to_embs(self, seqs, reduce_emb_mode=None):
        assert (type(seqs[0]) == list)
        embedded_seqs = []
        for seq in seqs:
            seq = self.tok_seq_to_embs(seq, reduce_emb_mode=reduce_emb_mode)
            embedded_seqs.append(seq)
        assert (len(seqs) == len(embedded_seqs))
        if reduce_emb_mode:
            embedded_seqs = numpy.array(embedded_seqs)
        return embedded_seqs

    def text_to_embs(self, seqs, reduce_emb_mode=None):
        '''tokenize string sequences and convert to word embeddings; if 'spacy' is given for word embeddings, encode directly through spacy API;
        if separate word embeddings given, use these embeddings; otherwise use existing self.word_embs'''
        embedded_seqs = []
        for seq in seqs:
            if hasattr(self, 'phrases') and self.phrases is not None:
                seq = combine_phrases_in_seq(seq, self.phrases,
                                             lemmatized=self.lemmatize)  # if sequences will be lemmatized, assume that given phrases are lemmatized
            seq = tokenize(seq, lemmatize=self.lemmatize, include_tags=self.include_tags,
                           prepend_start=self.prepend_start)
            seq = self.tok_seq_to_embs(seq, reduce_emb_mode=reduce_emb_mode)
            embedded_seqs.append(seq)
        assert (len(seqs) == len(embedded_seqs))
        if reduce_emb_mode:
            embedded_seqs = numpy.array(embedded_seqs)
        return embedded_seqs

    def num_seqs_to_bow(self, seqs):
        '''takes sequences of word indices as input and returns word count vectors'''
        count_vecs = []
        for seq in seqs:
            count_vec = numpy.bincount(numpy.array(seq), minlength=self.lexicon_size + 1)
            count_vecs.append(count_vec)
        count_vecs = numpy.array(count_vecs)
        count_vecs[:, 0] = 0  # don't include 0s in vector (0's are words that are not part of context)
        return count_vecs

    def text_to_bow(self, seqs):
        '''tokenize string sequences and convert to list of word indices'''
        # import pdb;pdb.set_trace()
        seqs = self.text_to_nums(seqs)
        bow_seqs = self.num_seqs_to_bow(seqs)
        return bow_seqs

    def decode_num_seqs(self, seqs, n_sents_per_seq=None, eos_tokens=[], detokenize=False, ents=[],
                        capitalize_ents=False, adapt_ents=False):
        if type(seqs[0]) not in (list, numpy.ndarray, tuple):
            seqs = [seqs]
        decoded_seqs = []
        # transform numerical seq back into string
        for seq_idx, seq in enumerate(seqs):
            seq = [self.lexicon_lookup[word] if self.lexicon_lookup[word] else self.unk_word for word in seq]
            if ents and adapt_ents:  # replace generated entities with those given in ents
                seq = adapt_tok_seq_ents(seq, ents=ents[seq_idx],
                                         sub_ent_probs=ent_counts_to_probs(self.filtered_ent_counts))
            if detokenize:  # apply rules for transforming token list into formatted sequence
                if ents and capitalize_ents:
                    seq = detokenize_tok_seq(seq, ents=ents[
                        seq_idx])  # , named_ents=named_ents) #detokenize; pass a list of words that should be capitalized
                else:
                    seq = detokenize_tok_seq(seq, ents=[])
            else:
                seq = " ".join(seq)  # otherwise just join tokens with whitespace between each
            if eos_tokens:  # if filter_n_sents is a number, filter generated sequence to only the first N=filter_n_sents sentences
                seq = filter_gen_seq(seq, eos_tokens=eos_tokens)
            elif n_sents_per_seq:
                seq = filter_gen_seq(seq, n_sents=n_sents_per_seq)
            decoded_seqs.append(seq)
        return decoded_seqs

    def nums_to_embs(self, seqs, reduce_emb_mode=None):  # , word_embs=None):
        embedded_seqs = []
        for seq in seqs:
            # convert to vectors rather than indices - if word not in lexicon represent with all zeros
            seq = [self.word_embs[self.lexicon_lookup[word]]
                   if self.lexicon_lookup[word] in self.word_embs
                   else numpy.zeros((self.n_embedding_nodes))
                   for word in seq]
            seq = numpy.array(seq)
            if reduce_emb_mode:  # combine embeddings of each sequence by averaging or summing them
                if reduce_emb_mode == 'mean':
                    seq = numpy.mean(seq, axis=0)
                elif reduce_emb_mode == 'sum':
                    seq = numpy.sum(seq, axis=0)
            embedded_seqs.append(seq)
        if reduce_emb_mode:
            embedded_seqs = numpy.array(embedded_seqs)
        return embedded_seqs

    def pad_embs(self, seqs, max_length=None):
        if not max_length:
            max_length = max([len(seq) for seq in seqs])

        padded_seqs = numpy.zeros((len(seqs), max_length, self.n_embedding_nodes))
        for seq_idx, seq in enumerate(seqs):
            for word_idx, word in enumerate(seq):
                padded_seqs[seq_idx, word_idx] = word

        return padded_seqs

    def seqs_to_feature_words(self, seqs, include_pos=('NOUN', 'PROPN')):
        '''input is sequences of where each sequence is a list of tokens where entities have already been replaced, if applicable;
        extract feature words from sequence; feature words are either named entities (i.e. has prefix 'ENT_') or whose pos tag is in include_pos;
        output will have sequence same length as original sequence but with None in places where word is not a context word'''

        feature_seqs = []
        for seq in seqs:
            seq_pos = [word.pos_ for word in encoder(seq)]
            seq = tokenize(seq)
            feature_seq = []
            for word, pos in zip(seq, seq_pos):
                if word in self.lexicon and (word.startswith('ENT_') or pos in include_pos):
                    feature_seq.append(word)
                else:
                    feature_seq.append(None)
            assert (len(feature_seq) == len(seq))
            feature_seqs.append(feature_seq)
        return feature_seqs

    def save(self):
        # save transformer to file
        if not os.path.isdir(self.filepath):
            os.mkdir(self.filepath)

        with open(self.filepath + '/transformer.pkl', 'wb') as f:
            pickle.dump(self, f)

        print('Saved transformer to', self.filepath + '/transformer.pkl')

    def __getstate__(self):
        # don't save embeddings
        state = dict((k, v) for (k, v) in self.__dict__.items() if k not in ('word_embs'))
        state.update({'word_embs': None})
        return state

    @classmethod
    def load(cls, filepath, word_embs=None):
        with open(filepath + '/transformer.pkl', 'rb') as f:
            transformer = pickle.load(f)
        transformer.word_embs = word_embs
        print('loaded transformer with', transformer.lexicon_size, 'words from', str(filepath) + '/transformer.pkl')
        return transformer


class SkipthoughtsTransformer(SequenceTransformer):
    def __init__(self, filepath, verbose=True):
        self.import_skipthoughts(filepath)
        self.filepath = filepath
        self.encoder = self.skipthoughts_module.load_model()
        self.encoder_dim = self.encoder['uoptions']['dim'] + 2 * self.encoder['boptions']['dim']
        self.verbose = verbose

    def import_skipthoughts(self, filepath):
        '''Import the skipthoughts model from the given directory filepath'''
        sys.path.append(filepath)
        try:
            self.skipthoughts_module = __import__('skipthoughts')
        except:
            print("Could not locate skipthoughts model in filepath", filepath)

    def text_to_embs(self, seqs, seqs_filepath=None):
        n_seqs = len(seqs)
        if type(seqs[0]) in (list, tuple):
            seq_length = numpy.array([len(seq) for seq in seqs])
            if numpy.all(seq_length[0] == seq_length[1:]):
                # every sequence has the same length
                seq_length = seq_length[0]
            # flatten seqs
            seqs = [sent for seq in seqs for sent in seq]
        else:
            seq_length = 1
        seqs_shape = (len(seqs), self.encoder_dim)
        if seqs_filepath:
            embedded_seqs = numpy.memmap(seqs_filepath, dtype='float',
                                         mode='w+', shape=seqs_shape)
        else:
            embedded_seqs = numpy.zeros(seqs_shape)

        chunk_size = 500000
        for seq_idx in range(0, len(seqs), chunk_size):
            # memory errors if encoding a large number of stories
            embedded_seqs[seq_idx:seq_idx + chunk_size] = self.skipthoughts_module.encode(self.encoder, seqs[
                                                                                                        seq_idx:seq_idx + chunk_size],
                                                                                          verbose=self.verbose)

        if type(seq_length) in (list, tuple, numpy.ndarray):
            # different lengths per sequence
            idxs = [numpy.sum(seq_length[:idx]) for idx in range(len(seq_length))] + [None]  # add -1 for last entry
            embedded_seqs = [embedded_seqs[idxs[start]:idxs[start + 1]] for start in range(len(idxs) - 1)]
        else:
            embedded_seqs = embedded_seqs.reshape(n_seqs, seq_length, self.encoder_dim)

        return embedded_seqs


class WordEmbeddings():
    def __init__(self, filepath):
        self.embs_filepath = filepath + "/embeddings.npy"
        self.lexicon_filepath = filepath + "/lexicon.pkl"
        self.embs = numpy.load(self.embs_filepath, mmap_mode='r')
        self.vector_size = self.embs.shape[-1]
        with open(self.lexicon_filepath, 'rb') as f:
            lexicon = pickle.load(f)
        self.lexicon = lexicon

    def __getitem__(self, word):
        word_emb = self.embs[self.lexicon[word]]
        return word_emb

    def __contains__(self, word):
        return word in self.lexicon

    @classmethod
    def load(cls, filepath):
        word_embs = WordEmbeddings(filepath)
        print("loaded word embeddings with", len(word_embs.lexicon), "words from", filepath)
        return word_embs
