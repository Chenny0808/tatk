import os
import torch
import logging
import torch.nn as nn
import json
import pickle
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.chdir(root_dir)
import sys
sys.path.append(os.getcwd())

from tatk.policy.rlmodule import MultiDiscretePolicy
from tatk.policy.vector.vector_multiwoz import MultiWozVector
from tatk.policy.mle.multiwoz.multiwoz_data_loader import PolicyDataLoaderMultiWoz
from tatk.util.train_util import to_device, init_logging_handler

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

class MLE_Trainer():
    def __init__(self, manager, cfg):
        self.data_train = manager.create_dataset_rl('train', cfg['batchsz'])
        self.data_valid = manager.create_dataset_rl('valid', cfg['batchsz'])
        self.data_test = manager.create_dataset_rl('test', cfg['batchsz'])
        self.save_dir = cfg['save_dir']
        self.print_per_batch = cfg['print_per_batch']
        
        voc_file = os.path.join(root_dir, 'data/multiwoz/sys_da_voc.txt')
        voc_opp_file = os.path.join(root_dir, 'data/multiwoz/usr_da_voc.txt')
        vector = MultiWozVector(voc_file, voc_opp_file)
        self.policy = MultiDiscretePolicy(vector.state_dim, cfg['h_dim'], vector.da_dim).to(device=DEVICE)
        self.policy.eval()
        self.policy_optim = torch.optim.Adam(self.policy.parameters(), lr=cfg['lr'])
        self.multi_entropy_loss = nn.MultiLabelSoftMarginLoss()
        
    def policy_loop(self, data):
        s, target_a = to_device(data)
        a_weights = self.policy(s)
        
        loss_a = self.multi_entropy_loss(a_weights, target_a)
        return loss_a
        
    def imitating(self, epoch):
        """
        pretrain the policy by simple imitation learning (behavioral cloning)
        """
        self.policy.train()
        a_loss = 0.
        for i, data in enumerate(self.data_train):
            self.policy_optim.zero_grad()
            loss_a = self.policy_loop(data)
            a_loss += loss_a.item()
            loss_a.backward()
            self.policy_optim.step()
            
            if (i+1) % self.print_per_batch == 0:
                a_loss /= self.print_per_batch
                logging.debug('<<dialog policy>> epoch {}, iter {}, loss_a:{}'.format(epoch, i, a_loss))
                a_loss = 0.
        
        if (epoch+1) % self.save_per_epoch == 0:
            self.save(self.save_dir, epoch, True)
        self.policy.eval()
    
    def imit_test(self, epoch, best):
        """
        provide an unbiased evaluation of the policy fit on the training dataset
        """
        a_loss = 0.
        for i, data in enumerate(self.data_valid):
            loss_a = self.policy_loop(data)
            a_loss += loss_a.item()
            
        a_loss /= len(self.data_valid)
        logging.debug('<<dialog policy>> validation, epoch {}, loss_a:{}'.format(epoch, a_loss))
        if a_loss < best:
            logging.info('<<dialog policy>> best model saved')
            best = a_loss
            self.save(self.save_dir, 'best', True)
            
        a_loss = 0.
        for i, data in enumerate(self.data_test):
            loss_a = self.policy_loop(data)
            a_loss += loss_a.item()
            
        a_loss /= len(self.data_test)
        logging.debug('<<dialog policy>> test, epoch {}, loss_a:{}'.format(epoch, a_loss))
        return best

    def save(self, directory, epoch, rl_only=False):
        if not os.path.exists(directory):
            os.makedirs(directory)

        torch.save(self.policy.state_dict(), directory + '/' + str(epoch) + '_mle.pol.mdl')

        logging.info('<<dialog policy>> epoch {}: saved network to mdl'.format(epoch))

    def load(self, filename):
        policy_mdl = filename + '_mle.pol.mdl'
        if os.path.exists(policy_mdl):
            self.policy.load_state_dict(torch.load(policy_mdl))
            logging.info('<<dialog policy>> loaded checkpoint from file: {}'.format(policy_mdl))
        
        best_pkl = filename + '.pkl'
        if os.path.exists(best_pkl):
            with open(best_pkl, 'rb') as f:
                best = pickle.load(f)
        else:
            best = float('inf')
        return best
        
if __name__ == '__main__':
    manager = PolicyDataLoaderMultiWoz()
    with open('config.json', 'r') as f:
        cfg = json.load(f)
    init_logging_handler(cfg['log_dir'])
    agent = MLE_Trainer(manager, cfg)
    
    best = float('inf')
    for e in range(cfg['epoch']):
        agent.imitating(e)
        best = agent.imit_test(e, best)