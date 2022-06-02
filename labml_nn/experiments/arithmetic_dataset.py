"""
This is based on code by [@gharik](https://twitter.com/gharik).
"""

import random
import string
from typing import List

import torch
from labml.logger import Text
from torch.utils.data import DataLoader, Dataset

from labml import monit, logger, tracker
from labml.configs import option
from labml_nn.experiments.nlp_autoregression import NLPAutoRegressionConfigs, transpose_batch


class ArithmeticDataset(Dataset):
    def __init__(self, seq_len: int, max_digits: int, n_sequences: int):
        self.n_sequences = n_sequences
        self.max_digits = max_digits
        self.seq_len = seq_len
        self.itos = list(string.digits + 'xe =\n?+;')
        self.stoi = {c: i for i, c in enumerate(self.itos)}

    @staticmethod
    def make_int(n_digits):
        res = 0
        for i in range(n_digits):
            d = random.randrange(1, 11) if i == 0 else random.randrange(0, 11)
            res = res * 10 + d

        return res

    @staticmethod
    def get_add_explanation(x, y):
        carry = 0
        e = 0
        explanation = []
        while x > 0 or y > 0 or carry > 0:
            rx, ry = x % 10, y % 10
            total = rx + ry + carry
            explanation.append(f"{rx}e{e}+{ry}e{e}+{carry}e{e}=={total}e{e}")
            x, y, carry = x // 10, y // 10, total // 10
            e += 1

        return ' '.join(explanation)

    # Make a problem with a pre_explanation or not
    def make_add_problem(self):
        x = self.make_int(n_digits=random.randrange(1, self.max_digits + 1))
        y = self.make_int(n_digits=random.randrange(1, self.max_digits + 1))

        if random.randrange(0, 5) < 1:
            return f"x={x}+{y}; x=={x + y}\n"
        else:
            explanation = self.get_add_explanation(x, y)
            return f"x={x}+{y}; {explanation} x=={x + y}\n"

    def get_qa(self):
        x = self.make_int(n_digits=random.randrange(1, self.max_digits + 1))
        y = self.make_int(n_digits=random.randrange(1, self.max_digits + 1))

        return f'x={x}+{y};', f'{x + y}'

    def get_packed_math_input(self):
        s_enc = []
        while len(s_enc) <= self.seq_len:
            s_part = self.make_add_problem()
            s_part_enc = self.encode('?' + s_part)
            s_enc = s_enc + s_part_enc
        return s_enc

    def encode(self, s: str):
        return [self.stoi[c] for c in s]

    def decode(self, arr: List[int]):
        return ''.join([self.itos[c] for c in arr])

    def __getitem__(self, idx):
        s = torch.tensor(self.get_packed_math_input())
        return s[:self.seq_len], s[1:self.seq_len + 1]

    def __len__(self):
        return self.n_sequences


class ArithmeticAutoregression(NLPAutoRegressionConfigs):
    max_digits: int = 4
    train_sequences_per_epoch: int = 2 ** 12
    train_loader: DataLoader = 'arithmetic_train_loader'
    n_tests: int = 32
    validator = None
    inner_iterations = 4

    n_tokens = len(ArithmeticDataset(1, 1, 1).itos)

    def sample(self):
        """
        ### Sampling function to generate samples periodically while training
        """

        if self.training_loop.idx < 1:
            return

        dataset = ArithmeticDataset(self.seq_len, self.max_digits, 1)
        qa = [dataset.get_qa() for _ in range(self.n_tests)]
        prompt = [p[0] for p in qa]

        data = torch.tensor([[dataset.stoi[p[0]] for p in prompt]])
        data = data.to(self.device)

        finished = torch.zeros((len(prompt),)).bool().to(self.device)
        new_line = dataset.stoi['\n']

        results = [p[0] for p in prompt]

        # Sample 25 tokens
        for i in monit.iterate('Sample', self.seq_len - 1):
            if finished.sum() == len(finished):
                continue

            # Tokenize the prompt
            # Get the model output
            output, *_ = self.model(data)
            # Get the model prediction (greedy)
            output = output[-1].argmax(dim=-1)

            finished = finished | (output == new_line)
            if finished.sum() == len(finished):
                continue

            for j, p in enumerate(prompt):
                if len(p) > i + 1:
                    output[j] = dataset.stoi[p[i + 1]]

            data = torch.cat([data, output[None, :]], dim=0)

            for j, c in enumerate(output):
                results[j] += dataset.itos[c]

        results = [r.split('\n')[0] for r in results]

        res_sample = results[0].split(';')
        logger.log([(res_sample[0], Text.key), (';', Text.subtle), (';'.join(res_sample[1:]), Text.none)])

        results = [r.split('x==')[-1] for r in results]

        correct = 0
        for r, _qa in zip(results, qa):
            if r == _qa[1]:
                correct += 1

        tracker.save('score', correct / len(results))


@option(ArithmeticAutoregression.train_loader)
def arithmetic_train_loader(c: ArithmeticAutoregression):
    return DataLoader(ArithmeticDataset(c.seq_len, c.max_digits, c.train_sequences_per_epoch),
                      batch_size=c.batch_size,
                      collate_fn=transpose_batch,
                      num_workers=4)


def _test():
    dataset = ArithmeticDataset(256, 8, 10)

    print(dataset.decode(dataset.get_packed_math_input()))


if __name__ == '__main__':
    _test()