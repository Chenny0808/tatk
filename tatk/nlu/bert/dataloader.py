import numpy as np
import torch
import random
from pytorch_pretrained_bert import BertTokenizer
import pickle
import os
import re


class Dataloader:
    def __init__(self, data, intent_vocab, tag_vocab):
        self.data = data
        self.intent_vocab = intent_vocab
        self.tag_vocab = tag_vocab
        self.intent_dim = len(intent_vocab)
        self.tag_dim = len(tag_vocab)
        self.id2intent = dict([(i, x) for i, x in enumerate(intent_vocab)])
        self.intent2id = dict([(x, i) for i, x in enumerate(intent_vocab)])
        self.id2tag = dict([(i, x) for i, x in enumerate(tag_vocab)])
        self.tag2id = dict([(x, i) for i, x in enumerate(tag_vocab)])
        self.tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
        self.intent_weight = [0]*len(self.intent2id)
        for key in self.data:
            for d in self.data[key]:
                word_seq, tag_seq, new2ori = self.bert_tokenize(d[0], d[1])
                d.append(new2ori)
                d.append(word_seq)
                d.append(self.seq_tag2id(tag_seq))
                d.append(self.seq_intent2id(d[2]))
                if key=='train':
                    for intent_id in d[-1]:
                        self.intent_weight[intent_id] += 1
        train_size = len(self.data['train'])
        for intent, inten_id in self.intent2id.items():
            self.intent_weight[inten_id] = np.log((train_size-self.intent_weight[inten_id])/self.intent_weight[inten_id])
            # print(intent, self.intent_weight[id], np.exp(self.intent_weight[id]))
        self.intent_weight = torch.tensor(self.intent_weight)

    def bert_tokenize(self, word_seq, tag_seq):
        split_tokens = []
        new_tag_seq = []
        new2ori = {}
        basic_tokens = self.tokenizer.basic_tokenizer.tokenize(' '.join(word_seq))
        accum = ''
        i, j = 0, 0
        for i, token in enumerate(basic_tokens):
            flag = (accum=='')
            if (accum+token).lower()==word_seq[j].lower():
                accum=''
            else:
                accum+=token
            first = True
            for sub_token in self.tokenizer.wordpiece_tokenizer.tokenize(basic_tokens[i]):
                new2ori[len(new_tag_seq)] = j
                split_tokens.append(sub_token)
                if flag and first:
                    new_tag_seq.append(tag_seq[j])
                    first=False
                else:
                    new_tag_seq.append('O')
            if accum=='':
                j += 1
        return split_tokens, new_tag_seq, new2ori

    def seq_tag2id(self, tags):
        return [self.tag2id[x] for x in tags]

    def seq_id2tag(self, ids):
        return [self.id2tag[x] for x in ids]

    def seq_intent2id(self, intents):
        return [self.intent2id[x] for x in intents]

    def seq_id2intent(self, ids):
        return [self.id2intent[x] for x in ids]

    def _pad_batch(self, batch_data):
        batch_size = len(batch_data)
        max_seq_len = max([len(x[-3]) for x in batch_data]) + 2
        word_seq_len = torch.zeros((batch_size), dtype=torch.long)
        word_mask_tensor = torch.zeros((batch_size, max_seq_len), dtype=torch.long)
        word_seq_tensor = torch.zeros((batch_size, max_seq_len), dtype=torch.long)
        tag_mask_tensor = torch.zeros((batch_size, max_seq_len), dtype=torch.long)
        tag_seq_tensor = torch.zeros((batch_size, max_seq_len), dtype=torch.long)
        intent_tensor = torch.zeros((batch_size, self.intent_dim), dtype=torch.long)
        for i in range(batch_size):
            _, _, _, _, words, tags, intents = batch_data[i]
            words = ['[CLS]'] + words + ['[SEP]']
            indexed_tokens = self.tokenizer.convert_tokens_to_ids(words)
            sen_len = len(words)
            word_seq_len[i] = sen_len
            word_seq_tensor[i, :sen_len] = torch.LongTensor([indexed_tokens])
            tag_seq_tensor[i, 1:sen_len-1] = torch.LongTensor(tags)
            word_mask_tensor[i, :sen_len] = torch.LongTensor([1] * sen_len)
            tag_mask_tensor[i, 1:sen_len-1] = torch.LongTensor([1] * (sen_len-2))
            for j in intents:
                intent_tensor[i, j] = 1
        return word_seq_tensor, tag_seq_tensor, intent_tensor, word_mask_tensor, tag_mask_tensor

    def get_train_batch(self, batch_size):
        batch_data = random.choices(self.data['train'], k=batch_size)
        return self._pad_batch(batch_data)

    def yield_batches(self, batch_size, data_key):
        batch_num = len(self.data[data_key]) // batch_size + 1
        for i in range(batch_num):
            batch_data = self.data[data_key][i * batch_size:(i + 1) * batch_size]
            yield self._pad_batch(batch_data), len(batch_data)

    def recover_intent(self, intent_logits, tag_logits, tag_mask_tensor, ori_word_seq, new2ori):
        # tag_logits = [batch_size, sequence_length, tag_dim]
        # intent_logits = [batch_size, intent_dim]
        # tag_mask_tensor = [batch_size, sequence_length]
        # new2ori = {(new_idx:old_idx),...} (after removing [CLS] and [SEP]
        batch_size = tag_logits.size(0)
        max_seq_len = tag_logits.size(1)
        batch_intents = []
        for i in range(batch_size):
            intents = []
            for j in range(self.intent_dim):
                if intent_logits[i,j] > 0.5:
                    intent, slot, value = re.split('[+*]',self.id2intent[j])
                    intents.append((intent, slot, value))
            tags = []
            for j in range(1,max_seq_len-1):
                if tag_mask_tensor[i,j] == 1:
                    value, tag_id = torch.max(tag_logits[i,j], dim=-1)
                    tags.append(self.id2tag[tag_id.item()])
            recover_tags = []
            for i, tag in enumerate(tags):
                if new2ori[i] >= len(recover_tags):
                    recover_tags.append(tag)
            i = 0
            while i < len(recover_tags):
                tag = recover_tags[i]
                if tag.startswith('B'):
                    intent, slot = tag[2:].split('+')
                    value = ori_word_seq[i]
                    j = i + 1
                    while j < len(recover_tags):
                        if recover_tags[j].startswith('I') and recover_tags[j][2:] == tag[2:]:
                            value += ' ' + ori_word_seq[j]
                            i += 1
                            j += 1
                        else:
                            break
                    intents.append((intent, slot, value))
                i += 1
            batch_intents.append(intents)
        return batch_intents

if __name__ == '__main__':
    data_dir = "multiwoz_usr_data"
    data = pickle.load(open(os.path.join(data_dir, 'data.pkl'), 'rb'))
    intent_vocab = pickle.load(open(os.path.join(data_dir, 'intent_vocab.pkl'), 'rb'))
    tag_vocab = pickle.load(open(os.path.join(data_dir, 'tag_vocab.pkl'), 'rb'))
    dataloader = Dataloader(data, intent_vocab, tag_vocab)